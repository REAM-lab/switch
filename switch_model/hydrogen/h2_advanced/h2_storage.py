# Copyright (c) 2016-2017 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
This module defines hydrogen storage technologies. It adds components for deciding how much energy to build into
storage, when to charge, energy accounting, etc.

INPUT FILE FORMAT
    Import storage parameters. Optional columns are noted with a *.

    h2_storage.csv
        H2_STORAGE_PROJECT, build_year, h2stor_load_zone, h2stor_life_years, h2stor_maximum_size_kg, 
        h2stor_is_predetermined, h2stor_predetermined_kg, h2stor_leakage_rate,
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

    H2_STORAGE_BLD_YRS is the set of H2 storage projects and years which they may be built 
    (investment periods and predetermined build years).

    h2stor_max_cycles_per_year[H2_STOR], if specified, restricts
    the number of charge/discharge cycles each H2 storage project can perform
    per year; one cycle is defined as discharging an amount of energy
    equal to the H2 storage capacity of the project.

    h2stor_land_use_rate[H2_STOR] is the amount of land used in square meters per MWh
    of storage for the given storage technology. Defaults to 0.
    
    h2stor_leakage_rate[H2_STOR] is the rate (as a percent fraction) in which H2 leaks out
    of storage. This is tracked in the H2TotalLeakage expression, which sums H2 leakage
    across all H2 system components.

    h2stor_overnight_cost_per_kg[(s, bld_yr) in H2_STORAGE_BLD_YRS] is the overnight 
    capital cost per kg of hydrogen capacity for building the given storage technology 
    installed in the given investment period.
    
    h2stor_fixed_om_cost_per_kg[(s, bld_yr) in H2_STORAGE_BLD_YRS] is the fixed O&M cost
    per kg of hydrogen capacity per year for building the given storage technology installed 
    in the given investment period.

    h2stor_predetermined_kg[(s, bld_yr) in
    PREDETERMINED_H2_STORAGE_BLD_YRS] is the amount of H2 storage that has either been
    installed previously, or is slated for installation and is not a free decision 
    variable. This is analogous to gen_predetermined_cap, but in units of hydrogen 
    storage capacity (kg) rather than power (MW).

    BuildH2Storage[(s, bld_yr) in H2_STORAGE_BLD_YRS]
    is a decision of how much energy capacity to build onto a storage project. This
    is analogous to BuildGen, but for kg of hydrogen rather than power.

    H2StorageEnergyInstallCosts[PERIODS] is an expression of the
    annual costs incurred by the BuildH2Storage decision.

    H2StorageCapacity[s, period] is an expression describing the
    cumulative available energy capacity of BuildH2Storage. This is
    analogous to GenCapacity.
    
    HGTS is defined in the switch_model.hydrogen.h2_advanced.h2_timescales module as
    the hydrogen_timeseries, which corresponds to the maximum frequency at which 
    hydrogen will be stored or withdrawn from storage. 
    TPS_IN_HGTS is also defined in the switch_model.hydrogen.h2_advanced.h2_timescales 
    module as the set of timepoints within each HGTS, indexed by HGTS.

    FillH2Storage[(s, tp) for tp in m.TPS_IN_HGTS[hgts]] is a dispatch decision of how 
    much to fill a hydrogen storage project in each timepoint.

    StorageNetFill[LOAD_ZONE, TIMEPOINT] is an expression describing the net/
    aggregate impact of FillH2Storage in each load zone and timepoint.

    Fill_Storage_Upper_Limit[(s, t) in H2_STORAGE_TPS]
    constrains FillH2Storage to available power capacity (accounting for
    gen_store_to_release_ratio)

    H2StateOfFill[(s, t) in H2_STORAGE_TPS] is a variable
    for tracking state of charge. This value stores the state of charge at
    the end of each timepoint for each storage project.

    Track_State_Of_Charge[(s, t) in H2_STORAGE_TPS] constrains
    H2StateOfFill based on the H2StateOfFill in the previous timepoint,
    FillH2Storage and DispatchGen.

    State_Of_Charge_Upper_Limit[(s, t) in H2_STORAGE_TPS]
    constrains H2StateOfFill based on installed energy capacity.

    H2StorageLandUseRate[s, period] is an expression for the amount of land used in
    meters squared per kg of H2 capacity for a given storage project during a given period.
    """
    mod.H2_STORAGE_PROJECTS = Set(dimen=1, input_file="h2_storage.csv")
    mod.h2stor_load_zone = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage.csv",
                              within=mod.LOAD_ZONES)
    mod.h2stor_life_years = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage.csv",
                            within=PositiveIntegers)
    mod.h2stor_leakage_rate = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage.csv",
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
	mod.h2stor_predetermined_kg = Param(
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
    
    def bounds_BuildH2Storage(model, s, bld_yr):
        if((s, bld_yr) in model.PREDETERMINED_H2_STOR_BLD_YRS):
            return (model.h2stor_predetermined_kg[s, bld_yr],
                    model.h2stor_predetermined_kg[s, bld_yr])
        elif(s in model.CAPACITY_LIMITED_H2_STOR):
            # This does not replace Max_Build_Potential because
            # Max_Build_Potential applies across all build years.
            return (0, model.h2stor_maximum_size_kg[s])
        else:
            return (0, None)
    mod.BuildH2Storage = Var(
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
    mod.BuildH2Storage_assign_default_value = BuildAction(
        mod.PREDETERMINED_H2_STOR_BLD_YRS,
        rule=get_assign_default_value_rule("BuildH2Storage", "h2stor_predetermined_kg"))

    mod.H2_STORAGE_PERIODS = Set(
        dimen=2,
        initialize=lambda m:
            [(s, p) for s in m.H2_STORAGE_PROJECTS for p in m.PERIODS_FOR_H2_STOR[s]])

    mod.H2StorCapacity = Expression(
        mod.H2_STORAGE_PROJECTS, mod.PERIODS,
        rule=lambda m, s, period: sum(
            m.BuildH2Storage[s, bld_yr]
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

    mod.h2stor_max_cycles_per_year = Param(
        mod.H2_STORAGE_PROJECTS,
        within=NonNegativeReals,
        input_file="h2_storage.csv",
        default=float("inf"),
    )
    mod.h2stor_land_use_rate = Param(
        mod.H2_STORAGE_PROJECTS,
        within=NonNegativeReals,
        default=0,
        input_file="h2_storage.csv",
        doc="Meters squared of land used per kg of H2 storage",
    )

    mod.H2_STORAGE_BLD_YRS = Set(
        dimen=2,
        initialize=lambda m: [
            (s, bld_yr) for s in m.H2_STORAGE_PROJECTS for bld_yr in m.BLD_YRS_FOR_H2_STOR[s]
        ],
    )
    mod.h2stor_overnight_cost_per_kg = Param(
        mod.H2_STORAGE_BLD_YRS,
        input_file="h2_storage.csv",
        within=NonNegativeReals,
    )
    mod.h2stor_fixed_om_cost_per_kg = Param(
        mod.H2_STORAGE_BLD_YRS,
        input_file="h2_storage.csv",
        within=NonNegativeReals,
    )
    mod.min_data_check("h2stor_overnight_cost_per_kg","h2stor_fixed_om_cost_per_kg")

    # Summarize capital costs of energy storage for the objective function
    mod.H2StorageFixedCost = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            sum(
                m.BuildH2Storage[s, bld_yr]
                * m.h2stor_overnight_cost_per_kg[s, bld_yr]
                * crf(m.interest_rate, m.gen_max_age[s])
                + m.BuildH2Storage[s, bld_yr] * h2stor_fixed_om_cost_per_kg[s, bld_yr]
                for bld_yr in m.BLD_YRS_FOR_H2_STOR_PERIOD[s, p]
            )
            for s in m.H2_STORAGE_PROJECTS
        ),
    )
    mod.Cost_Components_Per_Period.append("H2StorageFixedCost")

    mod.H2StorageCapacity = Expression(
        mod.H2_STORAGE_PROJECTS,
        mod.PERIODS,
        rule=lambda m, s, period: sum(
            m.BuildH2Storage[s, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_H2_STOR_PERIOD[s, period]
        ),
    )

    mod.H2StorageLandUse = Expression(
        mod.H2_STORAGE_PROJECTS,
        mod.PERIODS,
        rule=lambda m, s, p: m.h2stor_land_use_rate[s] * m.H2StorageCapacity[s, p],
    )
    
    mod.HGTS_FOR_H2_STOR = Set(
        mod.H2_STORAGE_PROJECTS,
        within=mod.TIMEPOINTS,
        initialize=lambda m, s: (
            hgts for p in m.PERIODS_FOR_H2_STOR[s] for hgts in m.HGTS_IN_PERIOD[p]
        )
    )
    
    mod.TPS_FOR_H2_STOR = Set(
        mod.H2_STORAGE_PROJECTS,
        within=mod.TIMEPOINTS,
        initialize=lambda m, s: (
            tp for hgts in m.HGTS_FOR_H2_STOR[s] for tp in m.TPS_IN_HGTS[hgts]
        )
    )

    mod.H2_STORAGE_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (s, tp) for s in m.H2_STORAGE_PROJECTS for tp in m.TPS_FOR_H2_STOR[s]
        ),
    )

    mod.FillH2Storage = Var(mod.H2_STORAGE_TPS, within=NonNegativeReals)

    # Summarize storage filling for the energy balance equations
    # (sum for a zone, not a net quantity for a project)
    def rule_f(m, z, t):
        # Construct and cache a set for summation as needed
        if not hasattr(m, "H2_Storage_Fill_Summation_dict"):
            m.H2_Storage_Fill_Summation_dict = collections.defaultdict(set)
            for s, t2 in m.H2_STORAGE_TPS:
                z2 = m.h2stor_load_zone[s]
                m.H2_Storage_Fill_Summation_dict[z2, t2].add(s)
        # Use pop to free memory
        relevant_projects_f = m.H2_Storage_Fill_Summation_dict.pop((z, t), {})
        return sum(m.FillH2Storage[s, t] for s in relevant_projects_f)

    mod.H2StorageTotalFill = Expression(mod.LOAD_ZONES, mod.TIMEPOINTS, rule=rule_f)
    # Register net charging with zonal energy balance. 
    mod.Zone_H2_Withdrawals.append("H2StorageTotalFill")

    def Fill_H2_Storage_Upper_Limit_rule(m, s, t):
        return (
            m.FillH2Storage[s, t]
            <= m.DispatchH2StorUpperLimit[s, t]
        )

    mod.Fill_Storage_Upper_Limit = Constraint(
        mod.H2_STORAGE_TPS, rule=Fill_H2_Storage_Upper_Limit_rule
    )
    
    mod.WithdrawH2Storage = Var(mod.H2_STORAGE_TPS, within=NonNegativeReals)
    
    # Summarize storage withdrawing for the energy balance equations
    # (sum for a zone, not a net quantity for a project)
    def rule_w(m, z, t):
        # Construct and cache a set for summation as needed
        if not hasattr(m, "H2_Storage_Withdraw_Summation_dict"):
            m.H2_Storage_Withdraw_Summation_dict = collections.defaultdict(set)
            for s, t2 in m.H2_STORAGE_TPS:
                z2 = m.h2stor_load_zone[s]
                m.H2_Storage_Withdraw_Summation_dict[z2, t2].add(s)
        # Use pop to free memory
        relevant_projects_w = m.H2_Storage_Withdraw_Summation_dict.pop((z, t), {})
        return sum(m.WithdrawH2Storage[s, t]*(1-m.h2stor_leakage_rate[s]) for s in relevant_projects_w)

    mod.H2StorageTotalWithdraw = Expression(mod.LOAD_ZONES, mod.TIMEPOINTS, rule=rule_w)
    # Register net charging with zonal energy balance. 
    mod.Zone_H2_Injections.append("H2StorageTotalWithdraw")
    
    # Summarize hydrogen leakage in storage
    # (sum for a zone)
    def rule_l(m, z, t):
        # Construct and cache a set for summation as needed
        if not hasattr(m, "H2_Storage_Leakage_Summation_dict"):
            m.H2_Storage_Leakage_Summation_dict = collections.defaultdict(set)
            for s, t2 in m.H2_STORAGE_TPS:
                z2 = m.h2stor_load_zone[s]
                m.H2_Storage_Leakage_Summation_dict[z2, t2].add(s)
        # Use pop to free memory
        relevant_projects_l = m.H2_Storage_Leakage_Summation_dict.pop((z, t), {})
        return sum(m.WithdrawH2Storage[s, t]*(m.h2stor_leakage_rate[s]) for s in relevant_projects_l)

    mod.H2StorageTotalLeakage = Expression(mod.LOAD_ZONES, mod.TIMEPOINTS, rule=rule_l)
    # Keep track of fugitive H2 emissions in each part of the H2 system
    mod.Zone_Fugitive_H2.append("H2StorageTotalLeakage")

    mod.H2StateOfFill = Var(mod.H2_STORAGE_TPS, within=NonNegativeReals)

    mod.H2StorageFlow = Expression(
        mod.H2_STORAGE_TPS,
        rule=lambda m, s, t: m.FillH2Storage[s, t]
        - m.WithdrawH2Storage[s, t],
    )

	def Track_State_Of_Fill_rule(m, s, t):
		# Carry-over is just previous fill level (no decay over time like batteries)
		carry_over_h2 = m.H2StateOfFill[s, m.tp_previous[t]]
	
		# Net storage change: fill minus gross withdrawal
		net_flow = m.FillH2Storage[s, t] - m.WithdrawH2Storage[s, t]
	
		return m.H2StateOfFill[s, t] == carry_over_h2 + net_flow
	
	mod.Track_State_Of_Fill = Constraint(
		mod.H2_STORAGE_TPS, rule=Track_State_Of_Fill_rule
	)

	def State_Of_Fill_Upper_Limit_rule(m, s, t):
		return m.H2StateOfFill[s, t] <= m.H2StorageCapacity[s, m.tp_period[t]]

	mod.State_Of_Fill_Upper_Limit = Constraint(
		mod.H2_STORAGE_TPS, rule=State_Of_Fill_Upper_Limit_rule
	)

    # some H2 storage techs can only complete the specified number of cycles per year, averaged over each period
    # (switch period, not hydrogen period, since the number of cycles is defined per year)
    mod.H2_Storage_Cycle_Limit = Constraint(
        mod.H2_STORAGE_PERIODS,
        rule=lambda m, s, p:
        # solvers sometimes perform badly with infinite constraint
        Constraint.Skip
        if m.h2stor_max_cycles_per_year[s] == float("inf")
        else (
            sum(
                m.WithdrawH2Storage[s, tp] * m.tp_duration_hrs[tp]
                for hgts in m.HGTS_IN_PERIOD[p] for tp in m.TPS_IN_HGTS[hgts]
            )
            <= m.h2stor_max_cycles_per_year[s]
            * m.H2StorageCapacity[s, p]
            * m.period_length_years[p]
        ),
    )

def post_solve(instance, outdir):
    """
    Export H2 storage build information to h2_storage_builds.csv,
    H2 storage capacity to h2_storage_capacity.csv, and H2 storage
    dispatch info to h2_storage_dispatch.csv
    """
    import switch_model.reporting as reporting

    # Write how much is built each build year for each project to h2_storage_builds.csv
    reporting.write_table(
        instance,
        instance.H2_STORAGE_BLD_YRS,
        output_file=os.path.join(outdir, "h2_storage_builds.csv"),
        headings=(
            "h2_storage_project",
            "build_year",
            "load_zone",
            "IncrementalCapacitykgH2",
        ),
        values=lambda m, s, bld_yr: (
            s,
            bld_yr,
            m.gen_load_zone[s],
            m.BuildH2Storage[s, bld_yr],
        ),
    )
    # Write the total capacity for each project at each period to h2_storage_capacity.csv
    reporting.write_table(
        instance,
        instance.H2_STORAGE_PERIODS,
        output_file=os.path.join(outdir, "h2_storage_capacity.csv"),
        headings=(
            "h2_storage_project",
            "period",
            "load_zone",
            "OnlineCapacitykgH2",
        ),
        values=lambda m, s, p: (
            s,
            p,
            m.gen_load_zone[s],
            m.H2StorageCapacity[s, p],
        ),
    )
    # Write how much is dispatched by each project at each time point to storage_dispatch.csv
    reporting.write_table(
        instance,
        instance.H2_STORAGE_TPS,
        output_file=os.path.join(outdir, "h2_storage_dispatch.csv"),
        # TODO renaming heading to timestamp (and update graphing accordingly)
        headings=(
            "h2_storage_project",
            "timepoint",
            "load_zone",
            "ChargeMW",
            "DischargeMW",
            "H2StateOfFill",
        ),
        values=lambda m, s, t: (
            s,
            m.tp_timestamp[t],
            m.gen_load_zone[s],
            m.FillH2Storage_kg_per_hr[s, t],
            m.WithdrawH2Storage_kg_per_hr[s, t],
            m.H2StateOfFill[s, t],
        ),
    )