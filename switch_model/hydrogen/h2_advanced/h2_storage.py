# Copyright (c) 2016-2017 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
This module defines hydrogen storage technologies. It adds components for deciding how much energy to build into
storage, when to charge, energy accounting, etc.

INPUT FILE FORMAT
    Import storage parameters. Optional columns are noted with a *.

    h2_storage.csv
        H2_STORAGE_PROJECT, build_year, h2stor_load_zone, h2stor_life_years, h2stor_maximum_size_kg, 
        h2stor_is_predetermined, h2stor_predetermined_cap_kg, h2_leakage_rate,
        h2stor_capital_cost_per_kg, h2stor_fixed_om_per_kg, h2stor_type

"""
import math

import pandas as pd
from scipy import fft

from pyomo.environ import *
import os, collections
from switch_model.financials import capital_recovery_factor as crf
from switch_model.tools.graph import graph
from switch_model.utilities.scaling import get_assign_default_value_rule

dependencies = (
    "switch_model.timescales",
    "switch_model.hydrogen.h2_advanced.h2_timescales",
    "switch_model.balancing.load_zones",
    "switch_model.financials",
    "switch_model.energy_sources.properties",
    "switch_model.hydrogen.h2_advanced.h2_production_build"
)


def define_components(mod):
    if not mod.options.no_hydrogen:
        define_hydrogen_components(mod)

def define_hydrogen_components(mod):
    """

    H2_STORAGE_PROJECT is the set of H2 storage candidate projects, which are of different types 
    (liquid_hydrogen_tank, gas_hydrogen_tank, hard_rock, salt_cavern).

    STORAGE_PROD_BLD_YRS is the subset of PROD_BLD_YRS, restricted
    to H2 storage projects.

    gen_storage_energy_to_power_ratio[H2_STOR], if specified, restricts
    the storage capacity (in MWh) to be a fixed multiple of the output
    power (in MW), i.e., specifies a particular number of hours of
    storage capacity. Omit this column or specify "." to allow Switch
    to choose the energy/power ratio. (Note: gen_storage_energy_overnight_cost
    or gen_overnight_cost should often be set to 0 when using this.)

    gen_storage_max_cycles_per_year[H2_STOR], if specified, restricts
    the number of charge/discharge cycles each storage project can perform
    per year; one cycle is defined as discharging an amount of energy
    equal to the storage capacity of the project.

    gen_self_discharge_rate[H2_STOR] is the fraction of the charge that is lost
    over a day. This is used for certain types of storage such as thermal energy
    storage that slowly loses its charge over time. Default is 0 (no self discharge).

    gen_land_use_rate[H2_STOR] is the amount of land used in square meters per MWh
    of storage for the given storage technology. Defaults to 0.

    gen_storage_energy_overnight_cost[(g, bld_yr) in
    STORAGE_PROD_BLD_YRS] is the overnight capital cost per MWh of
    energy capacity for building the given storage technology installed in the
    given investment period. This is only defined for storage technologies.
    Note that this describes the energy component and the overnight_cost
    describes the power component.

    gen_predetermined_storage_energy_mwh[(g, bld_yr) in
    PREDETERMINED_PROD_BLD_YRS] is the amount of storage that has either been
    installed previously, or is slated for installation and is not a free
    decision variable. This is analogous to gen_predetermined_cap, but in
    units of energy of storage capacity (MWh) rather than power (MW).

    BuildStorageEnergy[(g, bld_yr) in STORAGE_PROD_BLD_YRS]
    is a decision of how much energy capacity to build onto a storage
    project. This is analogous to BuildGen, but for energy rather than power.

    StorageEnergyInstallCosts[PERIODS] is an expression of the
    annual costs incurred by the BuildStorageEnergy decision.

    StorageEnergyCapacity[g, period] is an expression describing the
    cumulative available energy capacity of BuildStorageEnergy. This is
    analogous to GenCapacity.

    STORAGE_GEN_TPS is the subset of GEN_TPS,
    restricted to storage projects.

    ChargeStorage[(g, t) in STORAGE_GEN_TPS] is a dispatch
    decision of how much to charge a storage project in each timepoint.

    StorageNetCharge[LOAD_ZONE, TIMEPOINT] is an expression describing the
    aggregate impact of ChargeStorage in each load zone and timepoint.

    Charge_Storage_Upper_Limit[(g, t) in STORAGE_GEN_TPS]
    constrains ChargeStorage to available power capacity (accounting for
    gen_store_to_release_ratio)

    StateOfCharge[(g, t) in STORAGE_GEN_TPS] is a variable
    for tracking state of charge. This value stores the state of charge at
    the end of each timepoint for each storage project.

    Track_State_Of_Charge[(g, t) in STORAGE_GEN_TPS] constrains
    StateOfCharge based on the StateOfCharge in the previous timepoint,
    ChargeStorage and DispatchGen.

    State_Of_Charge_Upper_Limit[(g, t) in STORAGE_GEN_TPS]
    constrains StateOfCharge based on installed energy capacity.

    LandUseRate[g, period] is an expression for the amount of land used
    in meters squared for a given storage project during a given period.
    """
    mod.H2_STORAGE_PROJECTS = Set(dimen=1, input_file="h2_storage.csv")
    mod.h2stor_load_zone = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage.csv",
                              within=mod.LOAD_ZONES)
    mod.h2stor_life_years = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage.csv",
                            within=PositiveIntegers)
    mod.h2_leakage_rate = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage.csv",
                            within=PercentFraction)
    mod.CAPACITY_LIMITED_H2_STOR = Set(within=mod.H2_STORAGE_PROJECTS)
    mod.h2stor_maximum_size_kg = Param(
        mod.CAPACITY_LIMITED_H2_STOR, input_file="h2_storage.csv",
        input_optional=True, within=NonNegativeReals)

    mod.H2_STOR_BLD_YRS = Set(dimen=2, input_file="h2_storage.csv")
    mod.h2stor_is_predetermined = Param(mod.H2_STOR_BLD_YRS,
                                    input_file="h2_storage.csv",
                                    within=Boolean)
    def init_predetermined_h2_stor_bld_yrs(m):
    return [
        (s, bld_yr)
        for (s, bld_yr) in m.H2_STOR_BLD_YRS
        if m.h2stor_is_predetermined[s, bld_yr]
    ]
	mod.PREDETERMINED_H2_STOR_BLD_YRS = Set(
	    dimen=2,
	    initialize=init_predetermined_h2_stor_bld_yrs
	)
	mod.h2stor_predetermined_cap_kg = Param(
        mod.PREDETERMINED_H2_STOR_BLD_YRS,
        input_file="h2_storage.csv",
        within=NonNegativeReals)
    mod.BLD_YRS_FOR_H2_STOR = Set(
        mod.H2_STORAGE_PROJECTS,
        ordered=False,
        initialize=lambda m, s: set(
            bld_yr for (h2stor, bld_yr) in m.H2_STOR_BLD_YRS if h2stor == s
        )
    )
    mod.NEW_H2_STOR_BLD_YRS = Set(
        dimen=2,
        initialize=lambda m: m.H2_STOR_BLD_YRS - m.PREDETERMINED_H2_STOR_BLD_YRS)
    mod.h2stor_predetermined_cap_kg = Param(
        mod.PREDETERMINED_H2_STOR_BLD_YRS,
        input_file="h2_storage.csv",
        within=NonNegativeReals)

    def h2stor_build_can_operate_in_period(m, s, build_year, period):
        # If a period has the same name as a predetermined build year then we have a problem.
        # For example, consider what happens if we have both a period named 2020
        # and a predetermined build in 2020. In this case, "build_year in m.PERIODS"
        # will be True even if the project is a 2020 predetermined build.
        # This will result in the "online" variable being the start of the period rather
        # than the prebuild year which can cause issues such as the project retiring too soon.
        # To prevent this we've added the h2stor_no_predetermined_bld_yr_vs_period_conflict BuildCheck below.
        if build_year in m.PERIODS:
            online = m.period_start[build_year]
        else:
            online = build_year
        retirement = online + m.h2stor_life_years[s]
        # Previously the code read return online <= m.period_start[period] < retirement
        # However using the midpoint of the period as the "cutoff" seems more correct so
        # we've made the switch.
        return online <= m.period_start[period] + 0.5 * m.period_length_years[period] < retirement

    # This verifies that a predetermined build year doesn't conflict with a period since if that's the case
    # gen_build_can_operate_in_period will mistaken the prebuild for an investment build
    # (see note in h2stor_build_can_operate_in_period)
    mod.h2stor_no_predetermined_bld_yr_vs_period_conflict = BuildCheck(
        mod.PREDETERMINED_H2_STOR_BLD_YRS, mod.PERIODS,
        rule=lambda m, bld_yr, p: bld_yr != p
    )

    # The set of build years that could be online in the given period
    # for the given H2 storage project.
    mod.BLD_YRS_FOR_H2_STOR_PERIOD = Set(
        mod.H2_STORAGE_PROJECTS, mod.PERIODS,
        ordered=False,
        initialize=lambda m, s, period: set(
            bld_yr for bld_yr in m.BLD_YRS_FOR_H2_STOR[s]
            if h2stor_build_can_operate_in_period(m, s, bld_yr, period)))
    # The set of periods when a H2 storage tech is available to use
    mod.PERIODS_FOR_H2_STOR = Set(
        mod.H2_STORAGE_PROJECTS,
        initialize=lambda m, s: [p for p in m.PERIODS if len(m.BLD_YRS_FOR_H2_STOR_PERIOD[s, p]) > 0]
    )
    
    def bounds_BuildH2Stor(model, s, bld_yr):
        if((s, bld_yr) in model.PREDETERMINED_H2_STOR_BLD_YRS):
            return (model.h2stor_predetermined_cap_kg[s, bld_yr],
                    model.h2stor_predetermined_cap_kg[s, bld_yr])
        elif(s in model.CAPACITY_LIMITED_H2_STOR):
            # This does not replace Max_Build_Potential because
            # Max_Build_Potential applies across all build years.
            return (0, model.h2stor_maximum_size_kg[s])
        else:
            return (0, None)
    mod.BuildH2Stor = Var(
        mod.H2_STOR_BLD_YRS,
        within=NonNegativeReals,
        bounds=bounds_BuildGen)
    # Some projects are retired before the first study period, so they
    # don't appear in the objective function or any constraints.
    # In this case, pyomo may leave the variable value undefined even
    # after a solve, instead of assigning a value within the allowed
    # range. This causes errors in the Progressive Hedging code, which
    # expects every variable to have a value after the solve. So as a
    # starting point we assign an appropriate value to all the existing
    # projects here.
    mod.BuildH2Stor_assign_default_value = BuildAction(
        mod.PREDETERMINED_H2_STOR_BLD_YRS,
        rule=get_assign_default_value_rule("BuildH2Stor", "h2stor_predetermined_cap_kg"))

    mod.H2_STOR_PERIODS = Set(
        dimen=2,
        initialize=lambda m:
            [(s, p) for s in m.H2_STORAGE_PROJECTS for p in m.PERIODS_FOR_H2_STOR[s]])

    mod.H2StorCapacity = Expression(
        mod.H2_STORAGE_PROJECTS, mod.PERIODS,
        rule=lambda m, s, period: sum(
            m.BuildH2Stor[s, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_H2_STOR_PERIOD[s, period]))

    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    max_build_potential_scaling_factor = 1e-1
    mod.Max_H2Stor_Build_Potential = Constraint(
        mod.CAPACITY_LIMITED_H2_STOR, mod.PERIODS,
        rule=lambda m, s, p: (
                m.h2stor_maximum_size_kg[s] * max_build_potential_scaling_factor >= m.H2StorCapacity[
            s, p] * max_build_potential_scaling_factor))
# STOPPED EDITING HERE
    # TODO: rename to gen_charge_to_discharge_ratio?
    mod.gen_store_to_release_ratio = Param(
        mod.H2_STORAGE_PROJECTS,
        within=NonNegativeReals,
        input_file="generation_projects_info.csv",
        default=1.0,
    )
    mod.gen_storage_energy_to_power_ratio = Param(
        mod.H2_STORAGE_PROJECTS,
        input_file="generation_projects_info.csv",
        within=NonNegativeReals,
        default=float("inf"),
    )  # inf is a flag that no value is specified (nan and None don't work)
    mod.gen_storage_max_cycles_per_year = Param(
        mod.H2_STORAGE_PROJECTS,
        within=NonNegativeReals,
        input_file="generation_projects_info.csv",
        default=float("inf"),
    )
    mod.gen_self_discharge_rate = Param(
        mod.H2_STORAGE_PROJECTS,
        within=PercentFraction,
        default=0,
        input_file="generation_projects_info.csv",
        doc="Percent of stored energy lost per day.",
    )
    mod.gen_land_use_rate = Param(
        mod.H2_STORAGE_PROJECTS,
        within=NonNegativeReals,
        default=0,
        input_file="generation_projects_info.csv",
        doc="Meters squared of land used per MWh of storage",
    )

    mod.STORAGE_PROD_BLD_YRS = Set(
        dimen=2,
        initialize=lambda m: [
            (g, bld_yr) for g in m.H2_STOR for bld_yr in m.BLD_YRS_FOR_GEN[g]
        ],
    )
    mod.gen_storage_energy_overnight_cost = Param(
        mod.STORAGE_PROD_BLD_YRS,
        input_file="gen_build_costs.csv",
        within=NonNegativeReals,
    )
    mod.min_data_check("gen_storage_energy_overnight_cost")
    mod.gen_predetermined_storage_energy_mwh = Param(
        mod.PREDETERMINED_PROD_BLD_YRS,
        input_file="gen_build_predetermined.csv",
        within=NonNegativeReals,
    )
    mod.PREDETERMINED_STORAGE_PROD_BLD_YRS = Set(
        initialize=mod.PREDETERMINED_PROD_BLD_YRS,
        filter=lambda m, g, bld_yr: (g, bld_yr)
        in m.gen_predetermined_storage_energy_mwh,
    )

    def bounds_BuildStorageEnergy(m, g, bld_yr):
        if (g, bld_yr) in m.PREDETERMINED_STORAGE_PROD_BLD_YRS:
            return (
                m.gen_predetermined_storage_energy_mwh[g, bld_yr],
                m.gen_predetermined_storage_energy_mwh[g, bld_yr],
            )
        else:
            return (0, None)

    mod.BuildStorageEnergy = Var(
        mod.STORAGE_PROD_BLD_YRS,
        within=NonNegativeReals,
        bounds=bounds_BuildStorageEnergy,
    )

    # Some projects are retired before the first study period, so they
    # don't appear in the objective function or any constraints.
    # In this case, pyomo may leave the variable value undefined even
    # after a solve, instead of assigning a value within the allowed
    # range. This causes errors in the Progressive Hedging code, which
    # expects every variable to have a value after the solve. So as a
    # starting point we assign an appropriate value to all the existing
    # projects here.
    # TODO Don't include projects that are retired in the first study period in
    #   the model in the first place. Same thing in build.py with BuildGen.
    def BuildStorageEnergy_assign_default_value(m, g, bld_yr):
        m.BuildStorageEnergy[g, bld_yr] = m.gen_predetermined_storage_energy_mwh[
            g, bld_yr
        ]

    mod.BuildStorageEnergy_assign_default_value = BuildAction(
        mod.PREDETERMINED_STORAGE_PROD_BLD_YRS,
        rule=BuildStorageEnergy_assign_default_value,
    )

    # Summarize capital costs of energy storage for the objective function
    # Note: A bug in to 2.0.0b3 - 2.0.5, assigned costs that were several times
    # too high
    mod.StorageEnergyFixedCost = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            sum(
                m.BuildStorageEnergy[g, bld_yr]
                * m.gen_storage_energy_overnight_cost[g, bld_yr]
                * crf(m.interest_rate, m.gen_max_age[g])
                for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, p]
            )
            for g in m.H2_STOR
        ),
    )
    mod.Cost_Components_Per_Period.append("StorageEnergyFixedCost")

    # 2.0.0b3 code:
    # mod.StorageEnergyInstallCosts = Expression(
    # mod.PERIODS,
    # rule=lambda m, p: sum(m.BuildStorageEnergy[g, bld_yr] *
    #            m.gen_storage_energy_overnight_cost[g, bld_yr] *
    #            crf(m.interest_rate, m.gen_max_age[g])
    #            for (g, bld_yr) in m.STORAGE_PROD_BLD_YRS))

    mod.StorageEnergyCapacity = Expression(
        mod.H2_STORAGE_PROJECTS,
        mod.PERIODS,
        rule=lambda m, g, period: sum(
            m.BuildStorageEnergy[g, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_GEN_PERIOD[g, period]
        ),
    )

    mod.LandUse = Expression(
        mod.H2_STORAGE_PROJECTS,
        mod.PERIODS,
        rule=lambda m, g, p: m.gen_land_use_rate[g] * m.StorageEnergyCapacity[g, p],
    )

    mod.STORAGE_GEN_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (g, tp) for g in m.H2_STOR for tp in m.TPS_FOR_GEN[g]
        ),
    )

    mod.ChargeStorage = Var(mod.STORAGE_GEN_TPS, within=NonNegativeReals)

    # Summarize storage charging for the energy balance equations
    # TODO: rename this StorageTotalCharging or similar (to indicate it's a
    # sum for a zone, not a net quantity for a project)
    def rule(m, z, t):
        # Construct and cache a set for summation as needed
        if not hasattr(m, "Storage_Charge_Summation_dict"):
            m.Storage_Charge_Summation_dict = collections.defaultdict(set)
            for g, t2 in m.STORAGE_GEN_TPS:
                z2 = m.gen_load_zone[g]
                m.Storage_Charge_Summation_dict[z2, t2].add(g)
        # Use pop to free memory
        relevant_projects = m.Storage_Charge_Summation_dict.pop((z, t), {})
        return sum(m.ChargeStorage[g, t] for g in relevant_projects)

    mod.StorageNetCharge = Expression(mod.LOAD_ZONES, mod.TIMEPOINTS, rule=rule)
    # Register net charging with zonal energy balance. Discharging is already
    # covered by DispatchGen.
    mod.Zone_Power_Withdrawals.append("StorageNetCharge")

    # use fixed energy/power ratio (# hours of capacity) when specified
    mod.Enforce_Fixed_Energy_Storage_Ratio = Constraint(
        mod.STORAGE_PROD_BLD_YRS,
        rule=lambda m, g, y: Constraint.Skip
        if m.gen_storage_energy_to_power_ratio[g] == float("inf")  # no value specified
        else (
            m.BuildStorageEnergy[g, y]
            == m.gen_storage_energy_to_power_ratio[g] * m.BuildGen[g, y]
        ),
    )

    def Charge_Storage_Upper_Limit_rule(m, g, t):
        return (
            m.ChargeStorage[g, t]
            <= m.DispatchUpperLimit[g, t] * m.gen_store_to_release_ratio[g]
        )

    mod.Charge_Storage_Upper_Limit = Constraint(
        mod.STORAGE_GEN_TPS, rule=Charge_Storage_Upper_Limit_rule
    )

    mod.StateOfCharge = Var(mod.STORAGE_GEN_TPS, within=NonNegativeReals)

    mod.StorageFlow = Expression(
        mod.STORAGE_GEN_TPS,
        rule=lambda m, g, t: m.ChargeStorage[g, t] * m.gen_storage_efficiency[g]
        - m.DispatchGen[g, t] / m.gen_discharge_efficiency[g],
    )

    def Track_State_Of_Charge_rule(m, g, t):
        storage_efficiency = 1 - m.gen_self_discharge_rate[g]
        tp_duration_days = m.tp_duration_hrs[t] / 24
        # Energy in storage that remains from the energy in storage at the previous timepoint
        carry_over_energy = (
            m.StateOfCharge[g, m.tp_previous[t]]
            * storage_efficiency**tp_duration_days
        )
        # Energy change due to flow in or out of the battery (StorageFlow).
        flow_energy = m.StorageFlow[g, t] * (
            # If there's no decay, it's simply StorageFlow * tp_duration_hrs
            m.tp_duration_hrs[t]
            if storage_efficiency == 1
            else
            # If there is decay, we need to account for energy decay during the timepoint duration.
            # To derive the following expression, simply solve the differential equation:
            # dZ/dt = -rZ + StorageFlow
            # where r is the instantaneous decay rate, Z is the state of charge and t is time.
            # Note that exp(-24r) = (1 - daily_decay_rate).
            24
            * (storage_efficiency**tp_duration_days - 1)
            / math.log(storage_efficiency)
        )

        return m.StateOfCharge[g, t] == carry_over_energy + flow_energy

    mod.Track_State_Of_Charge = Constraint(
        mod.STORAGE_GEN_TPS, rule=Track_State_Of_Charge_rule
    )

    def State_Of_Charge_Upper_Limit_rule(m, g, t):
        return m.StateOfCharge[g, t] <= m.StorageEnergyCapacity[g, m.tp_period[t]]

    mod.State_Of_Charge_Upper_Limit = Constraint(
        mod.STORAGE_GEN_TPS, rule=State_Of_Charge_Upper_Limit_rule
    )

    # batteries can only complete the specified number of cycles per year, averaged over each period
    mod.Battery_Cycle_Limit = Constraint(
        mod.STORAGE_GEN_PERIODS,
        rule=lambda m, g, p:
        # solvers sometimes perform badly with infinite constraint
        Constraint.Skip
        if m.gen_storage_max_cycles_per_year[g] == float("inf")
        else (
            sum(
                m.DispatchGen[g, tp] * m.tp_duration_hrs[tp]
                for tp in m.TPS_IN_PERIOD[p]
            )
            <= m.gen_storage_max_cycles_per_year[g]
            * m.StorageEnergyCapacity[g, p]
            * m.period_length_years[p]
        ),
    )


def load_inputs(mod, switch_data, inputs_dir):
    # Base the set of storage projects on storage efficiency being specified.
    # TODO: define this in a more normal way
    switch_data.data()["H2_STOR"] = {
        None: list(switch_data.data(name="gen_storage_efficiency").keys())
    }


def post_solve(instance, outdir):
    """
    Export storage build information to storage_builds.csv,
    storage capacity to storage_capacity.csv, and storage
    dispatch info to storage_dispatch.csv
    """
    import switch_model.reporting as reporting

    # Write how much is built each build year for each project to storage_builds.csv
    reporting.write_table(
        instance,
        instance.STORAGE_PROD_BLD_YRS,
        output_file=os.path.join(outdir, "storage_builds.csv"),
        headings=(
            "generation_project",
            "build_year",
            "load_zone",
            "IncrementalPowerCapacityMW",
            "IncrementalEnergyCapacityMWh",
        ),
        values=lambda m, g, bld_yr: (
            g,
            bld_yr,
            m.gen_load_zone[g],
            m.BuildGen[g, bld_yr],
            m.BuildStorageEnergy[g, bld_yr],
        ),
    )
    # Write the total capacity for each project at each period to storage_capacity.csv
    reporting.write_table(
        instance,
        instance.STORAGE_GEN_PERIODS,
        output_file=os.path.join(outdir, "storage_capacity.csv"),
        headings=(
            "generation_project",
            "period",
            "load_zone",
            "OnlinePowerCapacityMW",
            "OnlineEnergyCapacityMWh",
        ),
        values=lambda m, g, p: (
            g,
            p,
            m.gen_load_zone[g],
            m.GenCapacity[g, p],
            m.StorageEnergyCapacity[g, p],
        ),
    )
    # Write how much is dispatched by each project at each time point to storage_dispatch.csv
    reporting.write_table(
        instance,
        instance.STORAGE_GEN_TPS,
        output_file=os.path.join(outdir, "storage_dispatch.csv"),
        # TODO renaming heading to timestamp (and update graphing accordingly)
        headings=(
            "generation_project",
            "timepoint",
            "load_zone",
            "ChargeMW",
            "DischargeMW",
            "StateOfCharge",
        ),
        values=lambda m, g, t: (
            g,
            m.tp_timestamp[t],
            m.gen_load_zone[g],
            m.ChargeStorage[g, t],
            m.DispatchGen[g, t],
            m.StateOfCharge[g, t],
        ),
    )