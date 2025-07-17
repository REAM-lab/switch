# Copyright (c) 2015-2019 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
Defines model components to describe H2 production project build-outs for
the Switch model. 

INPUT FILE FORMAT
    Import project-specific data from an input directory.

"""
from __future__ import division

import os, collections

# from pyomo.core.base.misc import sorted_robust
from pyomo.common.sorting import sorted_robust

from pyomo.environ import *
import pandas as pd

from switch_model.reporting import write_table
from switch_model.tools.graph import graph

dependencies = 'switch_model.timescales', 'switch_model.balancing.load_zones', \
               'switch_model.financials', 'switch_model.energy_sources.properties', \
               'switch_model.hydrogen.h2_advanced.h2_production_build', \
               'switch_model.hydrogen.h2_advanced.h2_timescales'

def define_components(m):
    if not m.options.no_hydrogen:
        define_hydrogen_components(m)

def define_hydrogen_components(m):
    """

    Adds components to a Pyomo abstract model object to describe the
    dispatch decisions and constraints of hydrogen production and storage
    projects. Unless otherwise stated, all hydrogen capacity is specified
    in units of MW of hydrogen and all sets and parameters are mandatory.
    
    *Note: H2 production projects are typically rated in kg of H2 per hour.
    We convert kg of H2 per hour to MW of H2 using the LHV of H2 of 33.32 kWh/kg
    from https://www.engineeringtoolbox.com/fuels-higher-calorific-values-d_169.html
    and https://sci-hub.kvnp.top/10.1016/j.ijhydene.2019.10.080. 
    (Example: Say a project is rated for 30,012 kg of H2 per hr. Then we have:
    30,012 kg_H2/s * 33.32 kWh/kg * 1 MW/1,000 kW =~ 1,000 MW of H2 or 1 GW of H2)

    PROD_TPS is a set of projects and timepoints in which
    they can be dispatched. A dispatch decisions is made for each member
    of this set. Members of this set can be abbreviated as (h, t) or
    (h, t).

    TPS_FOR_PROD[h] is a set array showing all timepoints when a
    project is active. These are the timepoints corresponding to
    PERIODS_FOR_GEN. This is the same data as PROD_TPS,
    but split into separate sets for each project.

    TPS_FOR_PROD_IN_PERIOD[h, period] is the same as
    TPS_FOR_PROD, but broken down by period. Periods when
    the project is inactive will yield an empty set.

    ProdCapacityInTP[(h, t) in PROD_TPS] is the same as
    GenCapacity but indexed by timepoint rather than period to allow
    more compact statements.

    DispatchProd[(h, t) in PROD_TPS] is the set
    of production dispatch decisions: how much average production in MW of
    H2 to produce in each timepoint. This value can be multiplied by the
    duration of the timepoint in hours to determine the amount of H2 produced
    by a project in a timepoint in MWh, which can be converted to kg of H2 using
    the LHV of H2 (33.32 kWh/kg).

    DispatchProdByFuel[(h, t, f) in PROD_TP_FUELS] is the H2 production
    in MW of H2 by each fuel-based project from each fuel during each timepoint.
    This is constrained such that the sum over all fuels gives
    DispatchProd.

    prod_av_outage_rate[h] describes the avergage outage
    rate for each hydrogen production project. This parameter
    is specified for individual projects in h2_production_Projects_info.csv. It 
    is originally defined in switch_model.hydrogen.h2_advanced.h2_production_build.

    prod_availability[h] describes the fraction of a time a project is
    expected to be available. This is derived from the average outage rate 
    of the project.

    prod_variable_om_per_kg[h] is the variable Operations and Maintenance
    costs (O&M) per kg of H2 produced for a given H2 production project. It 
    is originally defined in switch_model.hydrogen.h2_advanced.h2_production_build.

    mmbtu_fuel_per_kg_h2[h] is defined for fuel-based H2 production projects. 
    This describes the amount of fuel in mmbtu needed to produced 1 kg of H2.
    The default value is 0. It is originally defined in the
    switch_model.hydrogen.h2_advanced.h2_production_build module.
    
    mwh_per_kg_h2[h] is defined for all H2 production projects. This describes 
    the amount of electricity in MWh needed to produced 1 kg of H2.
    The default value is 0. For electrolyzers, this is the primary energy 
    source. For fuel-based hydrogen production technologies, this value is
    usually non-zero, as it represents the electrical load needed to operate 
    the hydrogen production facility. It is originally defined in the
    switch_model.hydrogen.h2_advanced.h2_production_build module.

    FUEL_BASED_PROD_TPS is a subset of PROD_TPS
    showing all times when fuel-consuming projects could be dispatched
    (used to identify timepoints when fuel use must match H2 production).

    PROD_TP_FUELS is a subset of PROD_TPS * FUELS,
    showing all the valid combinations of project, timepoint and fuel,
    i.e., all the times when each project could consume a fuel that is
    limited, costly or produces emissions.

    ProdFuelUseRate[(h, t, f) in PROD_TP_FUELS] is a
    variable that describes fuel consumption rate in MMBTU/h. This
    should be constrained to the fuel consumed by a project in each
    timepoint and can be calculated as DispatchProdByFuel [MW] *
    effective_heat_rate [MMBTU/MWh] -> [MMBTU/h]. The choice of how to
    constrain it depends on the treatment of unit commitment. Currently
    the project.no_commit module implements a simple treatment that
    ignores unit commitment and assumes a full load heat rate, while the
    project.unitcommit module implements unit commitment decisions with
    startup fuel requirements and a marginal heat rate.

    ProdDispatchEmissions[(h, t, f) in PROD_TP_FUELS] is the CO2
    emissions produced by dispatching a fuel-based project in units of
    metric tonnes CO2 per hour. This is derived from the fuel
    consumption ProdFuelUseRate and the fuel's direct carbon intensity. 
    This does not yet support multi-fuel generators.

    ProdDispatchEmissionsNOx[(h, t, f) in PROD_TP_FUELS],
    ProdDispatchEmissionsSO2[(h, t, f) in PROD_TP_FUELS], and
    ProdDispatchEmissionsCH4[(h, t, f) in PROD_TP_FUELS] are the nitrogen
    oxides, sulfur dioxide and methane emissions produced by dispatching
    a fuel-based project in units of metric tonnes per hour. These are
    derived using DispatchProdByFuel and the emissions factors associated
    with each prod_tech.

    ProdAnnualEmissions[p in PERIODS]:The hydrogen system's annual CO2 emissions, 
    in metric tonnes of CO2 per year.

    ProdAnnualEmissionsNOx[p in PERIODS], ProdAnnualEmissionsSO2[p in PERIODS] and
    ProdAnnualEmissionsCH4[p in PERIODS] are the hydrogen system's annual nitrogen 
    oxides, sulfur dioxide and methane emissions, in metric tonnes per year.
    
    ------------------------------------------
    
    Constraining dispatch decisions subject to available capacity:

    ProdDispatchUpperLimit[(h, t) in PROD_TPS] is an
    expression that defines the upper bounds of dispatch subject to
    installed capacity and average expected outage rates.

    ProdDispatchLowerLimit[(h, t) in PROD_TPS] in an
    expression that defines the lower bounds of dispatch, which is 0.

    Enforce_ProdDispatch_Lower_Limit[(h, t) in PROD_TPS] and
    Enforce_ProdDispatch_Upper_Limit[(h, t) in PROD_TPS] are
    constraints that limit DispatchProd to the upper and lower bounds
    defined above.

        ProdDispatchLowerLimit <= DispatchProd <= ProdDispatchUpperLimit

    ProdFuelUseRate_Calculate[(h, t, f) in PROD_TP_FUELS]
    calculates fuel consumption for the variable ProdFuelUseRate as
    DispatchProdByFuel * mmbtu_fuel_per_kg_h2. Using the LHV of H2 
    (33.32 kWh/kg of H2) and a conversion factor (1000 kWh/MWh), the units become:
    MW of H2 * (MMBtu / kg of H2) * (kg of H2 / kWh) * (kWh / MWh) = MMBTU / h

    """

    def period_active_gen_rule(m, period):
        if not hasattr(m, 'period_active_gen_dict'):
            m.period_active_gen_dict = collections.defaultdict(set)
            for (_h, _period) in m.GEN_PERIODS:
                m.period_active_gen_dict[_period].add(_g)
        result = m.period_active_gen_dict.pop(period)
        if len(m.period_active_gen_dict) == 0:
            delattr(m, 'period_active_gen_dict')
        return result
    mod.PROD_IN_PERIOD = Set(mod.PERIODS, ordered=False, initialize=period_active_gen_rule,
        doc="The set of projects active in a given period.")

    mod.TPS_FOR_PROD = Set(
        mod.GENERATION_PROJECTS,
        within=mod.TIMEPOINTS,
        initialize=lambda m, g: (
            tp for p in m.PERIODS_FOR_GEN[h] for tp in m.TPS_IN_PERIOD[p]
        )
    )

    def init(m, gen, period):
        try:
            d = m._TPS_FOR_PROD_IN_PERIOD_dict
        except AttributeError:
            d = m._TPS_FOR_PROD_IN_PERIOD_dict = dict()
            for _gen in m.PRODUCTION_PROJECTS:
                for t in m.TPS_FOR_PROD[_prod]:
                    d.setdefault((_gen, m.tp_period[t]), set()).add(t)
        result = d.pop((gen, period), set())
        if not d:  # all gone, delete the attribute
            del m._TPS_FOR_PROD_IN_PERIOD_dict
        return result
    mod.TPS_FOR_PROD_IN_PERIOD = Set(
        mod.GENERATION_PROJECTS, mod.PERIODS,
        ordered=False,
        within=mod.TIMEPOINTS, initialize=init)

    mod.PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.PRODUCTION_PROJECTS
                    for tp in m.TPS_FOR_PROD[h]))
    mod.VARIABLE_PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.VARIABLE_GENS
                    for tp in m.TPS_FOR_PROD[h]))
    mod.FUEL_BASED_PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.FUEL_BASED_GENS
                    for tp in m.TPS_FOR_PROD[h]))
    mod.PROD_TP_FUELS = Set(
        dimen=3,
        initialize=lambda m: (
            (h, t, f)
                for (h, t) in m.FUEL_BASED_PROD_TPS
                    for f in m.FUELS_FOR_GEN[h]))

    mod.ProdCapacityInTP = Expression(
        mod.PROD_TPS,
        rule=lambda m, g, t: m.GenCapacity[h, m.tp_period[t]])
    mod.DispatchProd = Var(
        mod.PROD_TPS,
        within=NonNegativeReals)

    ##########################################
    # Define DispatchProdByFuel
    #
    # Previously DispatchProdByFuel was simply a Variable for all the projects and a constraint ensured
    # that the sum of DispatchProdByFuel across all fuels was equal the total dispatch for that project.
    # However this approach creates extra variables in our model for projects that have only one fuel.
    # Although these extra variables likely get removed during Gurobi pre-solve, we've nonetheless
    # simplified the model here to reduce time in presolve and ensure the model is always
    # simplified regardless of the solving method.
    #
    # To do this we redefine DispatchProdByFuel to be an
    # expression that is equal to DispatchProdByFuelVar when we have multiple fuels but
    # equal to DispatchProd when we have only one fuel.

    # Define a set that is used to define DispatchProdByFuelVar
    mod.PROD_TP_FUELS_FOR_MULTIFUELS = Set(
        dimen=3,
        initialize=mod.PROD_TP_FUELS,
        filter=lambda m, g, t, f: g in m.MULTIFUEL_GENS,
        doc="Same as PROD_TP_FUELS but only includes multi-fuel projects"
    )
    # DispatchProdByFuelVar is a variable that exists only for multi-fuel projects.
    mod.DispatchProdByFuelVar = Var(mod.PROD_TP_FUELS_FOR_MULTIFUELS, within=NonNegativeReals)
    # DispatchProdByFuel_Constraint ensures that the sum of all the fuels is DispatchProd
    mod.DispatchProdByFuel_Constraint = Constraint(
        mod.FUEL_BASED_PROD_TPS,
        rule=lambda m, g, t:
        (Constraint.Skip if g not in m.MULTIFUEL_GENS
         else sum(m.DispatchProdByFuelVar[h, t, f] for f in m.FUELS_FOR_MULTIFUEL_GEN[h]) == m.DispatchProd[h, t])
    )

    # Define DispatchProdByFuel to equal the matching variable if we have many fuels but to equal
    # the total dispatch if we have only one fuel.
    mod.DispatchProdByFuel = Expression(
        mod.PROD_TP_FUELS,
        rule=lambda m, g, t, f: m.DispatchProdByFuelVar[h, t, f] if g in m.MULTIFUEL_GENS else m.DispatchProd[h, t]
    )

    # End Defining DispatchProdByFuel
    ##########################################

    # Only used to improve the performance of calculating ZoneTotalCentralDispatch and ZoneTotalDistributedDispatch
    mod.GENS_FOR_ZONE_TPS = Set(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        ordered=False,
        initialize=lambda m, z, t: set(g for h in m.GENS_IN_ZONE[z] if (h, t) in m.PROD_TPS)
    )

    # If we use the local_td module, divide distributed generation into a separate expression so that we can
    # put it in the distributed node's H2 balance equations
    using_local_td = hasattr(mod, "Distributed_Power_Injections")

    mod.ZoneTotalCentralDispatch = Expression(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        rule=lambda m, z, t: \
        sum(m.DispatchProd[h, t]
            for h in m.GENS_FOR_ZONE_TPS[z, t] if not using_local_td or not m.gen_is_distributed[h]) -
        sum(m.DispatchProd[h, t] * m.gen_ccs_energy_load[h]
            for h in m.CCS_EQUIPPED_GENS if g in m.GENS_FOR_ZONE_TPS[z, t]),
        doc="Net H2 from H2 production projects.")
    mod.Zone_Power_Injections.append('ZoneTotalCentralDispatch')

    def init_prod_availability(m, g):
        if m.gen_is_baseload[h]:
            return (
                (1 - m.gen_forced_outage_rate[h]) *
                (1 - m.gen_scheduled_outage_rate[h]))
        else:
            return (1 - m.gen_forced_outage_rate[h])
    mod.prod_availability = Param(
        mod.GENERATION_PROJECTS,
        within=NonNegativeReals,
        initialize=init_prod_availability)

    mod.VARIABLE_PROD_TPS_RAW = Set(
        dimen=2,
        within=mod.VARIABLE_GENS * mod.TIMEPOINTS,
        input_file='variable_capacity_factors.csv',
        input_optional=True
    )
    mod.gen_max_capacity_factor = Param(
        mod.VARIABLE_PROD_TPS_RAW,
        within=Reals,
        input_file='variable_capacity_factors.csv',
        validate=lambda m, val, g, t: -1 < val < 2)
    # Validate that a gen_max_capacity_factor has been defined for every
    # variable gen / timepoint that we need. Extra cap factors (like beyond an
    # existing plant's lifetime) shouldn't cause any problems.
    # This replaces: mod.min_data_check('gen_max_capacity_factor') from when
    # gen_max_capacity_factor was indexed by VARIABLE_PROD_TPS.
    mod.have_minimal_gen_max_capacity_factors = BuildCheck(
        mod.VARIABLE_PROD_TPS,
        rule=lambda m, g, t: (h,t) in m.VARIABLE_PROD_TPS_RAW)

    mod.ProdFuelUseRate = Var(
        mod.PROD_TP_FUELS,
        within=NonNegativeReals,
        doc=("Other modules constraint this variable based on DispatchProdByFuel and "
             "module-specific formulations of unit commitment and heat rates."))

    def ProdDispatchEmissions_rule(m, g, t, f):
        if g not in m.CCS_EQUIPPED_GENS:
            return (
                m.ProdFuelUseRate[h, t, f] *
                (m.f_co2_intensity[f] + m.f_upstream_co2_intensity[f]))
        else:
            ccs_emission_frac = 1 - m.gen_ccs_capture_efficiency[h]
            return (
                m.ProdFuelUseRate[h, t, f] *
                (m.f_co2_intensity[f] * ccs_emission_frac +
                 m.f_upstream_co2_intensity[f]))
    mod.ProdDispatchEmissions = Expression(
        mod.PROD_TP_FUELS,
        rule=ProdDispatchEmissions_rule)

    mod.ProdDispatchEmissionsNOx = Expression(
        mod.PROD_TP_FUELS,
        rule=(lambda m, g, t, f: m.DispatchProdByFuel[h, t, f] * m.f_nox_intensity[f]))

    mod.ProdDispatchEmissionsSO2 = Expression(
        mod.PROD_TP_FUELS,
        rule=(lambda m, g, t, f: m.DispatchProdByFuel[h, t, f] * m.f_so2_intensity[f]))

    mod.ProdDispatchEmissionsCH4 = Expression(
        mod.PROD_TP_FUELS,
        rule=(lambda m, g, t, f: m.DispatchProdByFuel[h, t, f] * m.f_ch4_intensity[f]))

    mod.ProdAnnualEmissions = Expression(mod.PERIODS,
        rule=lambda m, period: sum(
            m.ProdDispatchEmissions[h, t, f] * m.tp_weight_in_year[t]
            for (h, t, f) in m.PROD_TP_FUELS
            if m.tp_period[t] == period),
        doc="The system's annual CO2 emissions, in metric tonnes of CO2 per year.")

    mod.ProdAnnualEmissionsNOx = Expression(
        mod.PERIODS,
        rule=lambda m, period: sum(
            m.ProdDispatchEmissionsNOx[h, t, f] * m.tp_weight_in_year[t]
            for (h, t, f) in m.PROD_TP_FUELS
            if m.tp_period[t] == period),
        doc="The system's annual NOx emissions, in metric tonnes of NOx per year.")

    mod.ProdAnnualEmissionsSO2 = Expression(
        mod.PERIODS,
        rule=lambda m, period: sum(
            m.ProdDispatchEmissionsSO2[h, t, f] * m.tp_weight_in_year[t]
            for (h, t, f) in m.PROD_TP_FUELS
            if m.tp_period[t] == period),
        doc="The system's annual SO2 emissions, in metric tonnes of SO2 per year.")

    mod.ProdAnnualEmissionsCH4 = Expression(
        mod.PERIODS,
        rule=lambda m, period: sum(
            m.ProdDispatchEmissionsCH4[h, t, f] * m.tp_weight_in_year[t]
            for (h, t, f) in m.PROD_TP_FUELS
            if m.tp_period[t] == period),
        doc="The system's annual CH4 emissions, in metric tonnes of CH4 per year.")

    mod.ProdVariableOMCostsInTP = Expression(
        mod.TIMEPOINTS,
        rule=lambda m, t: sum(
            m.DispatchProd[h, t] * m.prod_variable_om_per_kg[h]
            for h in m.PROD_IN_PERIOD[m.tp_period[t]]),
        doc="Summarize costs for the objective function")
    mod.Cost_Components_Per_TP.append('ProdVariableOMCostsInTP')

    mod.BASELOAD_GEN_PERIODS = Set(
        dimen=2,
        initialize=lambda m:
            [(g, p) for g in m.BASELOAD_GENS for p in m.PERIODS_FOR_GEN[g]])
    mod.BASELOAD_GEN_TPS = Set(
        dimen=2,
        initialize=lambda m:
            [(g, t) for g, p in m.BASELOAD_GEN_PERIODS for t in m.TPS_IN_PERIOD[p]])

    mod.DispatchBaseloadByPeriod = Var(mod.BASELOAD_GEN_PERIODS)

    mod.DispatchUpperLimit = Expression(
        mod.GEN_TPS,
        rule=lambda m, g, t: m.GenCapacityInTP[g, t] * m.gen_availability[g] * (
            m.gen_max_capacity_factor[g, t] if m.gen_is_variable[g] else 1
        ))

    mod.Enforce_Dispatch_Baseload_Flat = Constraint(
        mod.BASELOAD_GEN_TPS,
        rule=lambda m, g, t:
            m.DispatchGen[g, t] == m.DispatchBaseloadByPeriod[g, m.tp_period[t]])

    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    mod.Enforce_Dispatch_Upper_Limit = Constraint(
        mod.GEN_TPS,
        rule=lambda m, g, t:
        m.DispatchGen[g, t] * 1e4 <= 1e4 * m.DispatchUpperLimit[g, t]
    )

    mod.GenFuelUseRate_Calculate = Constraint(
        mod.GEN_TP_FUELS,
        rule=lambda m, g, t, f: m.GenFuelUseRate[g,t,f] == m.DispatchGenByFuel[g,t,f] * m.gen_full_load_heat_rate[g]
    )


def post_solve(instance, outdir):
    """
    Exported files:

    dispatch-wide.csv - Dispatch results timepoints in "wide" format with
    timepoints as rows, generation projects as columns, and dispatch level
    as values

    dispatch.csv - Dispatch results in normalized form where each row
    describes the dispatch of a generation project in one timepoint.

    dispatch_annual_summary.csv - Similar to dispatch.csv, but summarized
    by generation technology and period.

    dispatch_zonal_annual_summary.csv - Similar to dispatch_annual_summary.csv
    but broken out by load zone.

    dispatch_annual_summary.pdf - A figure of annual summary data. Only written
    if the ggplot python library is installed.
    """
    sorted_gen = sorted_robust(instance.GENERATION_PROJECTS)
    write_table(
        instance, instance.TIMEPOINTS,
        output_file=os.path.join(outdir, "dispatch-wide.csv"),
        headings=("timestamp",) + tuple(sorted_gen),
        values=lambda m, t: (m.tp_timestamp[t],) + tuple(
            m.DispatchProd[p, t] if (p, t) in m.PROD_TPS
            else 0.0
            for p in sorted_gen
        )
    )
    del sorted_gen

    def c(func):
        return (value(func(h, t)) for g, t in instance.PROD_TPS)

    # Note we've refactored to create the Dataframe in one
    # line to reduce the overall memory consumption during
    # the most intensive part of post-solve (this function)
    dispatch_full_df = pd.DataFrame({
        "generation_project": c(lambda g, t: g),
        "gen_dbid": c(lambda g, t: instance.gen_dbid[h]),
        "gen_tech": c(lambda g, t: instance.gen_tech[h]),
        "gen_load_zone": c(lambda g, t: instance.gen_load_zone[h]),
        "gen_energy_source": c(lambda g, t: instance.gen_energy_source[h]),
        "timestamp": c(lambda g, t: instance.tp_timestamp[t]),
        "tp_weight_in_year_hrs": c(lambda g, t: instance.tp_weight_in_year[t]),
        "period": c(lambda g, t: instance.tp_period[t]),
        "is_renewable": c(lambda g, t: g in instance.VARIABLE_GENS),
        "DispatchProd_MW": c(lambda g, t: instance.DispatchProd[h, t]),
        "Curtailment_MW": c(lambda g, t:
                            value(instance.DispatchUpperLimit[h, t]) - value(instance.DispatchProd[h, t])),
        "Energy_GWh_typical_yr": c(lambda g, t:
                                   instance.DispatchProd[h, t] * instance.tp_weight_in_year[t] / 1000),
        "VariableOMCost_per_yr": c(lambda g, t:
                                   instance.DispatchProd[h, t] * instance.prod_variable_om_per_kg[h] *
                                   instance.tp_weight_in_year[t]),
        "ProdDispatchEmissions_tCO2_per_typical_yr": c(lambda g, t:
                                                   sum(
                                                       instance.ProdDispatchEmissions[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUELS_FOR_GEN[h]
                                                   ) if instance.gen_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tNOx_per_typical_yr": c(lambda g, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsNOx[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUELS_FOR_GEN[h]
                                                   ) if instance.gen_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tSO2_per_typical_yr": c(lambda g, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsSO2[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUELS_FOR_GEN[h]
                                                   ) if instance.gen_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tCH4_per_typical_yr": c(lambda g, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsCH4[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUELS_FOR_GEN[h]
                                                   ) if instance.gen_uses_fuel[h] else 0)
    })
    dispatch_full_df.set_index(["generation_project", "timestamp"], inplace=True)
    write_table(instance, output_file=os.path.join(outdir, "dispatch.csv"), df=dispatch_full_df)

    annual_summary = dispatch_full_df.groupby(['gen_tech', "gen_energy_source", "period"]).sum()
    write_table(instance, output_file=os.path.join(outdir, "dispatch_annual_summary.csv"),
                df=annual_summary,
                columns=["Energy_GWh_typical_yr", "VariableOMCost_per_yr",
                         "ProdDispatchEmissions_tCO2_per_typical_yr", "ProdDispatchEmissions_tNOx_per_typical_yr",
                         "ProdDispatchEmissions_tSO2_per_typical_yr", "ProdDispatchEmissions_tCH4_per_typical_yr"])

    zonal_annual_summary = dispatch_full_df.groupby(
        ['gen_tech', "gen_load_zone", "gen_energy_source", "period"]
    ).sum()
    write_table(
        instance,
        output_file=os.path.join(outdir, "dispatch_zonal_annual_summary.csv"),
        df=zonal_annual_summary,
        columns=["Energy_GWh_typical_yr", "VariableOMCost_per_yr",
                 "ProdDispatchEmissions_tCO2_per_typical_yr", "ProdDispatchEmissions_tNOx_per_typical_yr",
                 "ProdDispatchEmissions_tSO2_per_typical_yr", "ProdDispatchEmissions_tCH4_per_typical_yr"]
    )
