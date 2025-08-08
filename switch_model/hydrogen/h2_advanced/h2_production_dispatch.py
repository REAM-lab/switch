# Copyright (c) 2015-2019 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
Defines model components to describe H2 production project build-outs for
the Switch model. 

INPUT FILE FORMAT
    Import project-specific data from an input directory.
    
    Import prod_tech emissions data. To skip optional parameters such as
    upstream_co2_intensity, put a dot . in the relevant cell rather than
    leaving them blank. Leaving a cell blank will generate an error
    message like "IndexError: list index out of range". The following
    file is expected in the input directory. It is optional because
    you could have an all-electrolysis system. Units are all kg of pollutant
    per kg of h2 produced

    h2_emissions_factors.csv
        prod_tech, kg_co2_per_kg_h2, kg_ch4_per_kg_h2, kg_n2o_per_kg_h2, kg_so2_per_kg_h2, kg_nox_per_kg_h2, kg_pm10_per_kg_h2

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
    (Example: Say a project is rated for 30,012 kg of H2 per hour. Then we have:
    30,012 kg_H2/h * 33.32 kWh/kg * 1 MW/1,000 kW =~ 1,000 MW of H2 or 1 GW of H2)

    PROD_TPS is a set of projects and timepoints in which
    they can be dispatched. A dispatch decisions is made for each member
    of this set. Members of this set can be abbreviated as (h, t) or
    (h, t).

    TPS_FOR_PROD[h] is a set array showing all timepoints when a
    project is active. These are the timepoints corresponding to
    PERIODS_FOR_PROD. This is the same data as PROD_TPS,
    but split into separate sets for each project.

    TPS_FOR_PROD_IN_PERIOD[h, period] is the same as
    TPS_FOR_PROD, but broken down by period. Periods when
    the project is inactive will yield an empty set.

    ProdCapacityInTP[(h, t) in PROD_TPS] is the same as
    ProdCapacity but indexed by timepoint rather than period to allow
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

    ProdDispatchEmissionsCO2[(h, t, f) in PROD_TP_FUELS] is the CO2
    emissions produced by dispatching a fuel-based project in units of
    metric tonnes CO2 per hour. This is derived from the fuel
    consumption ProdFuelUseRate and the prod_tech's direct carbon intensity. 
    This does not yet support multi-fuel generators.

    ProdDispatchEmissionsNOx[(h, t, f) in PROD_TP_FUELS],
    ProdDispatchEmissionsSO2[(h, t, f) in PROD_TP_FUELS], and
    ProdDispatchEmissionsCH4[(h, t, f) in PROD_TP_FUELS] are the nitrogen
    oxides, sulfur dioxide and methane emissions produced by dispatching
    a fuel-based project in units of metric tonnes per hour. These are
    derived using DispatchProdByFuel and the emissions factors associated
    with each prod_tech.

    ProdAnnualEmissionsCO2[p in PERIODS]:The hydrogen system's annual CO2 emissions, 
    in metric tonnes of CO2 per year.

    ProdAnnualEmissionsNOx[p in PERIODS], ProdAnnualEmissionsSO2[p in PERIODS] and
    ProdAnnualEmissionsCH4[p in PERIODS] are the hydrogen system's annual nitrogen 
    oxides, sulfur dioxide and methane emissions, in metric tonnes per year.
    
    ------------------------------------------
    
    Constraining dispatch decisions subject to available capacity:

    ProdProdDispatchUpperLimit[(h, t) in PROD_TPS] is an
    expression that defines the upper bounds of dispatch subject to
    installed capacity and average expected outage rates.

    ProdDispatchLowerLimit[(h, t) in PROD_TPS] in an
    expression that defines the lower bounds of dispatch, which is 0.

    Enforce_ProdDispatch_Lower_Limit[(h, t) in PROD_TPS] and
    Enforce_ProdDispatch_Upper_Limit[(h, t) in PROD_TPS] are
    constraints that limit DispatchProd to the upper and lower bounds
    defined above.

        ProdDispatchLowerLimit <= DispatchProd <= ProdProdDispatchUpperLimit

    ProdFuelUseRate_Calculate[(h, t, f) in PROD_TP_FUELS]
    calculates fuel consumption for the variable ProdFuelUseRate as
    DispatchProdByFuel * mmbtu_fuel_per_kg_h2. Using the LHV of H2 
    (33.32 kWh/kg of H2) and a conversion factor (1000 kWh/MWh), the units become:
    [MW of H2] * [MMBtu / kg of H2] * [kg of H2 /33.32 kWh] * [1000 kWh / MWh] = MMBTU / h

    """

    def period_active_prod_rule(m, period):
        if not hasattr(m, 'period_active_prod_dict'):
            m.period_active_prod_dict = collections.defaultdict(set)
            for (_h, _period) in m.PROD_PERIODS:
                m.period_active_prod_dict[_period].add(_h)
        result = m.period_active_prod_dict.pop(period)
        if len(m.period_active_prod_dict) == 0:
            delattr(m, 'period_active_prod_dict')
        return result
    mod.PROD_IN_PERIOD = Set(mod.PERIODS, ordered=False, initialize=period_active_prod_rule,
        doc="The set of H2 production projects active in a given period.")

    mod.TPS_FOR_PROD = Set(
        mod.PRODUCTION_PROJECTS,
        within=mod.TIMEPOINTS,
        initialize=lambda m, p: (
            tp for p in m.PERIODS_FOR_PROD[h] for tp in m.TPS_IN_PERIOD[p]
        )
    )

    def init(m, prod, period):
        try:
            d = m._TPS_FOR_PROD_IN_PERIOD_dict
        except AttributeError:
            d = m._TPS_FOR_PROD_IN_PERIOD_dict = dict()
            for _prod in m.PRODUCTION_PROJECTS:
                for t in m.TPS_FOR_PROD[_prod]:
                    d.setdefault((_prod, m.tp_period[t]), set()).add(t)
        result = d.pop((prod, period), set())
        if not d:  # all gone, delete the attribute
            del m._TPS_FOR_PROD_IN_PERIOD_dict
        return result
    mod.TPS_FOR_PROD_IN_PERIOD = Set(
        mod.PRODUCTION_PROJECTS, mod.PERIODS,
        ordered=False,
        within=mod.TIMEPOINTS, initialize=init)

    mod.PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.PRODUCTION_PROJECTS
                    for tp in m.TPS_FOR_PROD[h]))
    mod.ELECTRICITY_BASED_PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.ELECTRICITY_BASED_PROD
                    for tp in m.TPS_FOR_PROD[h]))
    mod.FUEL_BASED_PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.FUEL_BASED_PROD
                    for tp in m.TPS_FOR_PROD[h]))
    mod.PROD_TP_FUELS = Set(
        dimen=3,
        initialize=lambda m: (
            (h, t, f)
                for (h, t) in m.FUEL_BASED_PROD_TPS
                    for f in m.FUEL_FOR_PROD[h]))
    mod.PROD_TP_PROD_TECH = Set(
        dimen=3,
        initialize=lambda m: (
            (h, t, tech)
                for (h, t) in m.FUEL_BASED_PROD_TPS
                    for tech in m.prod_tech[h]))

    mod.ProdCapacityInTP = Expression(
        mod.PROD_TPS,
        rule=lambda m, h, t: m.ProdCapacity[h, m.tp_period[t]])
    mod.DispatchProd = Var(
        mod.PROD_TPS,
        within=NonNegativeReals)

    # Define DispatchProdByFuel to simply equal the total dispatch (no multi-fuel capabilities).
    mod.DispatchProdByFuel = Expression(
        mod.PROD_TP_FUELS,
        rule=lambda m, h, t, f: m.DispatchProd[h, t]
    )

    # Only used to improve the performance of calculating ZoneTotalCentralH2Dispatch
    mod.PROD_FOR_ZONE_TPS = Set(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        ordered=False,
        initialize=lambda m, z, t: set(h for h in m.PROD_IN_ZONE[z] if (h, t) in m.PROD_TPS)
    )

    mod.ZoneTotalCentralH2Dispatch = Expression(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        rule=lambda m, z, t: \
        sum(m.DispatchProd[h, t]
            for h in m.PROD_FOR_ZONE_TPS[z, t]),
        doc="Total H2 from H2 production projects per zone at each timepoint.")
    mod.Zone_H2_Injections.append('ZoneTotalCentralH2Dispatch')

    def init_prod_availability(m, h):
        return (1 - m.prod_av_outage_rate[h])
    mod.prod_availability = Param(
        mod.PRODUCTION_PROJECTS,
        within=NonNegativeReals,
        initialize=init_prod_availability)

    mod.ProdFuelUseRate = Var(
        mod.PROD_TP_FUELS,
        within=NonNegativeReals,
        doc=("[MMBTU/h] Other modules constrain this variable based on DispatchProdByFuel."))

    # GREENHOUSE GASES (LHV of H2 = 33.32 kWh/kg)
	mod.kg_co2_per_kg_h2 = Param(mod.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		input_file="h2_emissions_factors.csv", input_column="kg_co2_per_kg_h2")
	mod.kg_ch4_per_kg_h2 = Param(mod.PRODUCTION_TECHNOLOGIES, within=Reals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_ch4_per_kg_h2")
	mod.kg_n2o_per_kg_h2 = Param(mod.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_n2o_per_kg_h2")
	
	# CRITERIA AIR POLLUTANTS (LHV of H2 = 33.32 kWh/kg)
	mod.kg_so2_per_kg_h2 = Param(mod.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_so2_per_kg_h2")
	mod.kg_nox_per_kg_h2 = Param(mod.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_nox_per_kg_h2")
	mod.kg_pm10_per_kg_h2 = Param(mod.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_pm10_per_kg_h2")
	
	# EMISSIONS EXPRESSIONS (metric tonnes = kg * 1e-3) [metric tonnes per hour]
	def ProdDispatchEmissions_rule_co2(m, h, t, f):
		return (m.ProdFuelUseRate[h, t, f] * (1 / mmbtu_fuel_per_kg_h2[h]) * m.kg_co2_per_kg_h2[prod_tech[h]] * 1e-3)
	mod.ProdDispatchEmissionsCO2 = Expression(mod.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_co2)
	
	def ProdDispatchEmissions_rule_ch4(m, h, t, f):
		return (m.ProdFuelUseRate[h, t, f] * (1 / mmbtu_fuel_per_kg_h2[h]) * m.kg_ch4_per_kg_h2[prod_tech[h]] * 1e-3)
	mod.ProdDispatchEmissionsCH4 = Expression(mod.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_ch4)
	
	def ProdDispatchEmissions_rule_n2o(m, h, t, f):
		return (m.ProdFuelUseRate[h, t, f] * (1 / mmbtu_fuel_per_kg_h2[h]) * m.kg_n2o_per_kg_h2[prod_tech[h]] * 1e-3)
	mod.ProdDispatchEmissionsN2O = Expression(mod.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_n2o)
	
	def ProdDispatchEmissions_rule_so2(m, h, t, f):
		return (m.ProdFuelUseRate[h, t, f] * (1 / mmbtu_fuel_per_kg_h2[h]) * m.kg_so2_per_kg_h2[prod_tech[h]] * 1e-3)
	mod.ProdDispatchEmissionsSO2 = Expression(mod.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_so2)
	
	def ProdDispatchEmissions_rule_nox(m, h, t, f):
		return (m.ProdFuelUseRate[h, t, f] * (1 / mmbtu_fuel_per_kg_h2[h]) * m.kg_nox_per_kg_h2[prod_tech[h]] * 1e-3)
	mod.ProdDispatchEmissionsNOx = Expression(mod.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_nox)
	
	def ProdDispatchEmissions_rule_pm10(m, h, t, f):
		return (m.ProdFuelUseRate[h, t, f] * (1 / mmbtu_fuel_per_kg_h2[h]) * m.kg_pm10_per_kg_h2[prod_tech[h]] * 1e-3)
	mod.ProdDispatchEmissionsPM10 = Expression(mod.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_pm10)

	# [metric tonnes per year]
	mod.ProdAnnualEmissionsCO2 = Expression(mod.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsCO2[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual CO2 emissions, in metric tonnes per year.")

	mod.ProdAnnualEmissionsCH4 = Expression(mod.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsCH4[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual CH4 emissions, in metric tonnes per year.")

	mod.ProdAnnualEmissionsN2O = Expression(mod.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsN2O[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual N2O emissions, in metric tonnes per year.")

	mod.ProdAnnualEmissionsSO2 = Expression(mod.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsSO2[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual SO2 emissions, in metric tonnes per year.")

	mod.ProdAnnualEmissionsNOx = Expression(mod.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsNOx[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual NOx emissions, in metric tonnes per year.")

	mod.ProdAnnualEmissionsPM10 = Expression(mod.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsPM10[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual PM10 emissions, in metric tonnes per year.")

	# DispatchProd tells us the average production in units of MW of H2 produced in each timepoint. 
	# This value is multiplied by the duration of the timepoint in hours to determine the amount of 
	# H2 produced by a project over the duration of timepoint t in units of MWh. This is converted 
	# to kg of H2 using the LHV of H2 (33.32 kWh/kg) and the conversion factor of 1000 kWh/MWh.

    mod.ProdVariableOMCostsInPeriod = Expression(
		mod.PERIODS,
		rule=lambda m, p: sum(
			m.DispatchProd[h, t] * m.tp_weight_in_year[t] * (1000 / 33.32) * m.prod_variable_om_per_kg[h]
			for t in m.TPS_IN_PERIOD[p]
			for h in m.PROD_IN_PERIOD[m.tp_period[t]]
		),
		doc="Summarize variable OM costs per kg of H2 produced in each period for the objective function"
	)
	mod.Cost_Components_Per_Period.append('ProdVariableOMCostsInPeriod')

    mod.ProdDispatchUpperLimit = Expression(
        mod.PROD_TPS,
        rule=lambda m, h, t: m.ProdCapacityInTP[h, t] * m.prod_availability[h])

    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    mod.Enforce_Prod_Dispatch_Upper_Limit = Constraint(
        mod.PROD_TPS,
        rule=lambda m, h, t:
        m.DispatchProd[h, t] * 1e4 <= 1e4 * m.ProdDispatchUpperLimit[h, t]
    )
	# [MW of H2] * [MMBtu / kg of H2] * [kg of H2 /33.32 kWh] * [1000 kWh / MWh] = MMBTU / h
    mod.ProdFuelUseRate_Calculate = Constraint(
        mod.PROD_TP_FUELS,
        rule=lambda m, h, t, f: m.ProdFuelUseRate[h, t, f] == m.DispatchProdByFuel[h, t, f] * m.mmbtu_fuel_per_kg_h2[h] * (1000 / 33.32)
    )

def post_solve(instance, outdir):
    """
    Exported files:

    h2-prod-dispatch-wide.csv - Dispatch results timepoints in "wide" format with
    timepoints as rows, production projects as columns, and dispatch level
    as values

    h2_prod_dispatch.csv - Dispatch results in normalized form where each row
    describes the dispatch of a production project in one timepoint.

    h2_prod_dispatch_annual_summary.csv - Similar to h2_prod_dispatch.csv, but summarized
    by production technology and period.

    h2_prod_dispatch_zonal_annual_summary.csv - Similar to h2_prod_dispatch_annual_summary.csv
    but broken out by load zone.

    """
    sorted_prod = sorted_robust(instance.PRODUCTION_PROJECTS)
    write_table(
        instance, instance.TIMEPOINTS,
        output_file=os.path.join(outdir, "h2-prod-dispatch-wide.csv"),
        headings=("timestamp",) + tuple(sorted_prod),
        values=lambda m, t: (m.tp_timestamp[t],) + tuple(
            m.DispatchProd[h, t] if (h, t) in m.PROD_TPS
            else 0.0
            for h in sorted_prod
        )
    )
    del sorted_prod

    def c(func):
        return (value(func(h, t)) for h, t in instance.PROD_TPS)

    # Note we've refactored to create the Dataframe in one
    # line to reduce the overall memory consumption during
    # the most intensive part of post-solve (this function)
    prod_dispatch_full_df = pd.DataFrame({
        "production_project": c(lambda h, t: h),
        "prod_tech": c(lambda h, t: instance.prod_tech[h]),
        "prod_load_zone": c(lambda h, t: instance.prod_load_zone[h]),
        "prod_energy_source": c(lambda h, t: instance.prod_energy_source[h]),
        "timestamp": c(lambda h, t: instance.tp_timestamp[t]),
        "tp_weight_in_year_hrs": c(lambda h, t: instance.tp_weight_in_year[t]),
        "period": c(lambda h, t: instance.tp_period[t]),
        "DispatchProd_MW_H2": c(lambda h, t: instance.DispatchProd[h, t]),
        "Curtailment_MW_H2": c(lambda h, t:
                            value(instance.ProdDispatchUpperLimit[h, t]) - value(instance.DispatchProd[h, t])),
        "H2_produced_tonne_typical_yr": c(lambda h, t:
                                   instance.DispatchProd[h, t] * instance.tp_weight_in_year[t] / 33.32),
        "ProdVariableOMCost_per_yr": c(lambda h, t:
                                   instance.DispatchProd[h, t] * instance.tp_weight_in_year[t] * (1000 / 33.32) *
                                   instance.prod_variable_om_per_kg[h]),
        "ProdDispatchEmissions_tCO2_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsCO2[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUEL_FOR_PROD[h]
                                                   ) if instance.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tCH4_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsCH4[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUEL_FOR_PROD[h]
                                                   ) if instance.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tN2O_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsN2O[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUEL_FOR_PROD[h]
                                                   ) if instance.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tSO2_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsSO2[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUEL_FOR_PROD[h]
                                                   ) if instance.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tNOx_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsNOx[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUEL_FOR_PROD[h]
                                                   ) if instance.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tPM10_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       instance.ProdDispatchEmissionsPM10[h, t, f] *
                                                       instance.tp_weight_in_year[t]
                                                       for f in instance.FUEL_FOR_PROD[h]
                                                   ) if instance.prod_uses_fuel[h] else 0)
    })
    prod_dispatch_full_df.set_index(["production_project", "timestamp"], inplace=True)
    write_table(instance, output_file=os.path.join(outdir, "h2_prod_dispatch.csv"), df=prod_dispatch_full_df)

    prod_annual_summary = prod_dispatch_full_df.groupby(['prod_tech', "prod_energy_source", "period"]).sum()
    write_table(instance, output_file=os.path.join(outdir, "h2_prod_dispatch_annual_summary.csv"),
                df=prod_annual_summary,
                columns=["H2_produced_tonne_typical_yr", "ProdVariableOMCost_per_yr",
                         "ProdDispatchEmissions_tCO2_per_typical_yr", "ProdDispatchEmissions_tCH4_per_typical_yr",
                         "ProdDispatchEmissions_tN2O_per_typical_yr", "ProdDispatchEmissions_tSO2_per_typical_yr",
                         "ProdDispatchEmissions_tNOx_per_typical_yr", "ProdDispatchEmissions_tPM10_per_typical_yr"])

    prod_zonal_annual_summary = prod_dispatch_full_df.groupby(
        ['prod_tech', "prod_load_zone", "prod_energy_source", "period"]
    ).sum()
    write_table(
        instance,
        output_file=os.path.join(outdir, "h2_prod_dispatch_zonal_annual_summary.csv"),
        df=prod_zonal_annual_summary,
        columns=["H2_produced_tonne_typical_yr", "ProdVariableOMCost_per_yr",
                 "ProdDispatchEmissions_tCO2_per_typical_yr", "ProdDispatchEmissions_tCH4_per_typical_yr",
                 "ProdDispatchEmissions_tN2O_per_typical_yr", "ProdDispatchEmissions_tSO2_per_typical_yr",
                 "ProdDispatchEmissions_tNOx_per_typical_yr", "ProdDispatchEmissions_tPM10_per_typical_yr"]
    )
