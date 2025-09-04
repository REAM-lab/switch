"""
Defines hydrogen (H2) production projects build-outs for advanced hydrogen model.

INPUT FILE FORMAT
    Import data describing H2 production project builds and dispatch. The following 
    files are expected in the input directory.

    h2_production_projects_info.csv has mandatory and optional columns. 
    You may drop optional columns entirely or mark blank
    values with a dot '.' for select rows for which the column does not
    apply. Mandatory columns are:
        PRODUCTION_PROJECT, prod_tech, prod_load_zone, prod_energy_source,
        prod_max_age, mmbtu_fuel_per_kg_h2, mwh_per_kg_h2, prod_variable_om_per_kg
    Optional columns are:
        prod_av_outage_rate, prod_capacity_limit_mw, prod_ccs_equipped, 
        prod_is_onsite, prod_onsite_GENERATION_PROJECT, prod_onsite_gen_tech

    The following file lists existing builds of H2 production projects, and is
    optional for simulations where there is no existing capacity:

    h2_prod_predetermined.csv.csv
        PRODUCTION_PROJECT, build_year, prod_predetermined_cap_mw

    The following file is mandatory, because it sets cost parameters for
    both existing and new project buildouts:

    h2_prod_build_costs.csv
        PRODUCTION_PROJECT, build_year, prod_overnight_cost_per_mw, prod_fixed_om_per_mw_yr
        
    h2_demand.csv:
    This input file imports the hydrogen demand profile for the hydrogen balancing 
    constraint (H2 supply = demand in each load zone at each timepoint). Each row 
    lists a load zone (LOAD_ZONE), a timepoint (TIMEPOINT) (which has a frequency of 
    at most 1-hour), and the corresponding hydrogen demand (zone_demand_mw_h2) in units
    of MW of H2. This is assumed to be the hourly average demand for the duration of 
    that timepoint. For example, if a timepoint has a duration of 4 hours and a load 
    zone at that timepoint has a demand of 10 MW of H2, then we assume the demand in that
    zone is 10 MW for the whole 4 hours corresponding to that timepoint.
    
    h2_demand.csv:
        LOAD_ZONE, TIMEPOINT, zone_demand_mw_h2
    
    h2_emissions_factors.csv:
    This input file imports prod_tech emissions factor data. To skip optional 
    parameters such as kg_ch4_per_kg_h2, put a dot . in the relevant cell rather 
    than leaving them blank. Leaving a cell blank will generate an error
    message like "IndexError: list index out of range". The following
    file is expected in the input directory. It is optional because
    you could have an all-electrolysis system. Units are all kg of pollutant
    per kg of h2 produced

    h2_emissions_factors.csv
        prod_tech, kg_co2_per_kg_h2, 
    Optional columns are:
        kg_ch4_per_kg_h2, kg_n2o_per_kg_h2, kg_so2_per_kg_h2, kg_nox_per_kg_h2, kg_pm10_per_kg_h2
        
    h2_carbon_policies.csv:
    This input file imports carbon limits by period, including Global Warming Potential (GWP) 
    values for CH4, N2O, and H2. GWP describes the coefficient for Greenhouse Gas (GHG) emissions
    such that the multiplication of the coefficients and the GHG totals equals the CO2 equivalent 
    emissions in terms of potential to contribute to global warming. The standard is to reference
    GWP values from the latest IPCC report. GWPs of 0 or emissions factors of 0 for GHGs other than 
    CO2 would "tell" the model not to consider those GHGs. Note that the CO2 limit listed in this 
    file applies to the hydrogen sector only (not electricity). Fo the purpose of consolidating 
    input files, all parameters listed in this file are indexed by period, although only the 
    h2_carbon_cap_tco2_per_yr actually depends on the period. This input file is optional.
    
    h2_carbon_policies.csv:
    PERIOD, h2_carbon_cap_tco2_per_yr, ch4_gwp, n2o_gwp, h2_gwp
"""

from __future__ import division
import os, collections
import pandas as pd

from pyomo.common.sorting import sorted_robust
from pyomo.environ import *
from switch_model.financials import capital_recovery_factor as crf
from switch_model.reporting import write_table
from switch_model.utilities.scaling import get_assign_default_value_rule
    
dependencies = 'switch_model.timescales', 'switch_model.balancing.load_zones', \
               'switch_model.financials', 'switch_model.energy_sources.properties', \
               'switch_model.hydrogen.advanced.h2_timescales'

def define_hydrogen_dynamic_lists(m):
    """
    Zone_H2_Injections and Zone_H2_Withdrawals are lists of
    components that contribute to load-zone level H2 balance equations.
    sum(Zone_H2_Injections[z,t]) == sum(Zone_H2_Withdrawals[z,t])
        for all z,t
    Other modules may append to either list, as long as the components they
    add are indexed by [zone, timepoint] and have units of MW of H2. Other modules
    often include Expressions to summarize decision variables on a zonal basis.
    
    *Note: MW of hydrogen is a measure of H2 production (typically thought of in terms of kg of H2 per hour)
    converted to MW using the LHV of H2 of 33.32 kWh/kg from 
    https://www.engineeringtoolbox.com/fuels-higher-calorific-values-d_169.html
    and https://sci-hub.kvnp.top/10.1016/j.ijhydene.2019.10.080. 
    (Example: Say a H2 production project dispatches 30,012 kg of H2 per hour at a particular tp. Then we have:
    30,012 kg_H2/h * 33.32 kWh/kg * 1 MW/1,000 kW =~ 1,000 MW of H2 or 1 GW of H2)
    
    Period_Fugitive_H2 tracks total fugitive H2 emissions (leaked H2) in metric tonnes per period.
    
    """
    m.Zone_H2_Injections = []
    m.Zone_H2_Withdrawals = []
    m.Period_Fugitive_H2 = []

def define_dynamic_hydrogen_components(m):
    """
    Adds components to a Pyomo abstract model object to enforce hydrogen
    demand and production balance at the level of load zone buses. Unless
    otherwise stated, all terms describing H2 flows are in units of MW of 
    H2 all terms describing H2 energy are in units of kg of H2.

    Zone_H2_Balance[load_zone, timepoint] is a constraint that mandates
    conservation of H2 in every load zone and timepoint. This constraint
    sums the model components in the lists Zone_H2_Injections and
    Zone_H2_Withdrawals - each of which is indexed by (z, t) and has units 
    of MW of H2 - and ensures they are equal. The term tp_duration_hrs
    is factored out of the equation for brevity.
    """

    m.Zone_H2_Balance = Constraint(
        m.ZONE_TIMEPOINTS,
        rule=lambda m, z, t: (
            sum(
                getattr(m, component)[z, t]
                for component in m.Zone_H2_Injections
            ) == sum(
                getattr(m, component)[z, t]
                for component in m.Zone_H2_Withdrawals))
    )
    
    m.System_Fugitive_H2 = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            getattr(m, component)[p]
                for component in m.Period_Fugitive_H2
        )
    )

def define_hydrogen_components(m):
    """
    Adds components to a Pyomo abstract model object to describe the building of hydrogen production
    capacity in each of the load zones. 
    
    This module is 1 of 5 modules that make up a complex hydrogen system model with the following components: 
    	1. h2_production_build.py: H2 production- electrolyzer, SMR, SMR + CCS, gasification, (and pyrolysis?)
    	2. H2 transport- pipelines (and liquid trucks?)
    	3. H2 storage- liquid tank storage, geological storage  
    	4. H2 system components- liquefier (and compressors?)
    	5. H2 system dispatch- production and demand balance
    	
    *Note: H2 production projects are all rated by MW of hydrogen, which is a measure of
    its maximum production capacity in kg of H2 per hour, converted to MW using the LHV 
    of H2 of 33.32 kWh/kg from https://www.engineeringtoolbox.com/fuels-higher-calorific-values-d_169.html
    and https://sci-hub.kvnp.top/10.1016/j.ijhydene.2019.10.080. 
    (Example: Say a project is rated for 8.34 kg of H2 per s. Then we have:
    8.34 kg_H2/s * 33.32 kWh/kg * 1 MW/1,000 kW * 3,600 s/hr =~ 1,000 MW of H2 or 1 GW of H2)
    
    ------------------------------------------
    
    PRODUCTION_PROJECTS is the set of H2 production projects that
    have been built or could potentially be built. A project is a combination
    of H2 production technology, load zone and location. A particular build-out
    of a project should also include the year in which construction was
    complete and additional capacity came online. Members of this set are
    abbreviated as prod in parameter names and h in indexes (think of the mnemonic: 
    h for H2). Use of p instead of g is discouraged because p is reserved for period.

    prod_tech[h] describes what kind of technology an H2 production project is
    using (electrolyzer, SMR, etc.).

    prod_load_zone[h] is the load zone this H2 production project is built in.

    PROD_IN_ZONE[z in LOAD_ZONES] is an indexed set that lists all
    H2 production projects within each load zone.

    CAPACITY_LIMITED_PROD is the subset of PRODUCTION_PROJECTS that are
    capacity limited. This can be specified for any H2 production project. 
    Some existing or proposed H2 production projects may have upper bounds 
    on increasing capacity or replacing capacity as it is retired based on 
    permits or local air quality regulations.

    prod_capacity_limit_mw[h] is defined for H2 production projects that are
    capacity limited. This describes the maximum possible capacity of an H2 
    production project in units of megawatts or megawatts of H2. See the *note 
    above for more information on the capacity units.
    
    mmbtu_fuel_per_kg_h2[h] is defined for fuel-based H2 production projects. 
    This describes the amount of fuel in mmbtu needed to produced 1 kg of H2.
    The default value is 0.
    
    mwh_per_kg_h2[h] is defined for all H2 production projects. This describes 
    the amount of electricity in MWh needed to produced 1 kg of H2.
    The default value is 0. For electrolyzers, this is the primary energy 
    source. For fuel-based hydrogen production technologies, this value is
    usually non-zero, as it represents the electrical load needed to operate 
    the hydrogen production facility.
    
    prod_leakage_rate[h] is the leakage rate which specifies the % of H2 produced that
    is leaked as fugitive H2 emissions during production. 
    
    prod_ccs_equipped[h] is a TRUE/FALSE parameter which specifies if the H2 production
    project is equipped with Carbon Capture and Sequestration (CCS). 
    
    *Note we do not define a CCS capture efficiency or energy penalty like we do for 
    electricity generators with CCS. This is because we instead use a heating value 
    that already accounts for the energy penalty from the CCS and a CO2 emission factor 
    that already accounts for the capture efficiency of the CCS for each CCS-eqipped H2 
    production project.
    
    -- CONSTRUCTION --

    PROD_BLD_YRS is a two-dimensional set of H2 production projects and the
    years in which construction or expansion occured or can occur. You
    can think of a project as a physical site that can be built out over
    time. BuildYear is the year in which construction is completed and
    new capacity comes online, not the year when constrution begins.
    BuildYear will be in the past for existing projects and will be the
    first year of an investment period for new projects. Investment
    decisions are made for each project/invest period combination. This
    set is derived from other parameters for all new construction. This
    set also includes entries for existing projects that have already
    been built and planned projects whose capacity buildouts have already been
    decided; information for legacy projects come from other files
    and their build years will usually not correspond to the set of
    investment periods. There are two recommended options for
    abbreviating this set for denoting indexes: typically this should be
    written out as (h, build_year) for clarity, but when brevity is
    more important (h, b) is acceptable.

    NEW_PROD_BLD_YRS is a subset of PROD_BLD_YRS that only
    includes projects that have not yet been constructed. This is
    derived by joining the set of PRODUCTION_PROJECTS with the set of
    NEW_PROD_BLD_YRS using H2 production technology.

    PREDETERMINED_PROD_BLD_YRS is a subset of PROD_BLD_YRS that
    only includes existing or planned projects that are not subject to
    optimization.

    prod_predetermined_cap_mw[(h, build_year) in PREDETERMINED_PROD_BLD_YRS] is
    a parameter that describes how much capacity was built in the past
    for existing projects, or is planned to be built for future projects, in MW of
    H2 for all H2 production projects.

    BuildProd[h, build_year] is a decision variable that describes
    how much capacity of a project to install in a given period. This also
    stores the amount of capacity that was installed in existing projects
    that are still online [MW of H2].

    ProdCapacity[h, period] is an expression that returns the total
    capacity online in a given period. This is the sum of installed capacity
    minus all retirements [MW of H2].

    Max_Prod_Build_Potential[h] is a constraint defined for each project
    that enforces maximum capacity limits for resource-limited projects.

        ProdCapacity <= prod_capacity_limit_mw

    Note the option to specify a minimum capacity per project was removed to 
    avoid binary variables.

    --- OPERATIONS ---

    PERIODS_FOR_PROD_BLD_YR[h, build_year] is an indexed
    set that describes which periods a given project build will be
    operational.

    BLD_YRS_FOR_PROD_PERIOD[h, period] is a complementary
    indexed set that identify which build years will still be online
    for the given project in the given period. For some project-period
    combinations, this will be an empty set.

    PROD_PERIODS describes periods in which H2 production projects
    could be operational. Unlike the related sets above, it is not
    indexed. Instead it is specified as a set of (g, period)
    combinations useful for indexing other model components.

    --- BUILD COSTS ---

	The following cost components are defined for each project and build
    year. These parameters will always be available, but will typically
    be populated by the generic costs specified in h2_prod_build_costs.csv
    input file.

    prod_overnight_cost_per_mw[h, build_year] is the overnight capital cost per
    MW of capacity for building a project in the given period. By
    "installed in the given period", I mean that it comes online at the
    beginning of the given period and construction starts before that.

    prod_fixed_om_per_mw_yr[h, build_year] is the annual fixed Operations and
    Maintenance costs (O&M) per MW of capacity for given project that
    was installed in the given period.
    
    prod_variable_om_per_kg[h] is the variable Operations and Maintenance
    costs (O&M) per kg of H2 produced for a given H2 production project.

    -- Derived cost parameters --

    prod_capital_cost_annual[h, build_year] is the annualized loan
    payments for a project's capital and connection costs in units of
    $/MW per year. This is specified in non-discounted real dollars in a
    future period, not real dollars in net present value.

    ProdCapitalCosts[h, period] is the total annual capital
    costs incurred by a project in a period. This reflects all of the builds 
    are operational in the given period. This is an expression that reflect
    decision variables.
    
    ProdFixedOMCosts[h, period] is the total annual fixed O&M
    costs incurred by a project in a period. This reflects all of the builds 
    are operational in the given period. This is an expression that reflect
    decision variables.

    TotalProdFixedCosts[period] is the sum of
    ProdCapitalCosts[h, period] and ProdFixedOMCosts[h, period] for all projects 
    that could be online in the target period. This aggregation is performed for 
    the benefit of the objective function.
    
    --- DISPATCH ---
    
    The second portion of the code adds components to a Pyomo abstract model 
    object to describe the dispatch decisions and constraints of hydrogen 
    production projects. Unless otherwise stated, all hydrogen capacity is specified
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
    is originally defined in switch_model.hydrogen.advanced.h2_production_build.

    prod_availability[h] describes the fraction of a time a project is
    expected to be available. This is derived from the average outage rate 
    of the project.

    prod_variable_om_per_kg[h] is the variable Operations and Maintenance
    costs (O&M) per kg of H2 produced for a given H2 production project. It 
    is originally defined in switch_model.hydrogen.advanced.h2_production_build.

    m.mmbtu_fuel_per_kg_h2[h] is defined for fuel-based H2 production projects. 
    This describes the amount of fuel in mmbtu needed to produced 1 kg of H2.
    The default value is 0. It is originally defined in the
    switch_model.hydrogen.advanced.h2_production_build module.
    
    mwh_per_kg_h2[h] is defined for all H2 production projects. This describes 
    the amount of electricity in MWh needed to produced 1 kg of H2.
    The default value is 0. For electrolyzers, this is the primary energy 
    source. For fuel-based hydrogen production technologies, this value is
    usually non-zero, as it represents the electrical load needed to operate 
    the hydrogen production facility. It is originally defined in the
    switch_model.hydrogen.advanced.h2_production_build module.

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
    DispatchProdByFuel * m.mmbtu_fuel_per_kg_h2. Using the LHV of H2 
    (33.32 kWh/kg of H2) and a conversion factor (1000 kWh/MWh), the units become:
    [MW of H2] * [MMBtu / kg of H2] * [kg of H2 /33.32 kWh] * [1000 kWh / MWh] = MMBTU / h
        
    """
	# H2 PRODUCTION TECHNOLOGY DETAILS

    # This set is defined by h2_production_projects_info.csv, which is the set of H2 production technology IDs
    m.PRODUCTION_PROJECTS = Set(dimen=1, input_file="h2_production_projects_info.csv")

    m.prod_tech = Param(m.PRODUCTION_PROJECTS,
                         input_file="h2_production_projects_info.csv",
                         within=Any)

    m.PRODUCTION_TECHNOLOGIES = Set(ordered=False, initialize=lambda m:
                                    {m.prod_tech[h] for h in m.PRODUCTION_PROJECTS})

    m.prod_load_zone = Param(m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
                              within=m.LOAD_ZONES)

    m.prod_energy_source = Param(m.PRODUCTION_PROJECTS, within=Any,
                                  input_file="h2_production_projects_info.csv",
                                  validate=lambda m, val, p: val in m.ENERGY_SOURCES or val == "multiple")

    m.prod_max_age = Param(m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
    						within=PositiveIntegers)

    m.prod_av_outage_rate = Param(m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
                                          within=PercentFraction, default=0)

    m.min_data_check('PRODUCTION_PROJECTS', 'prod_tech', 'prod_energy_source',
                       'prod_load_zone', 'prod_max_age')

    """Construct PROD_* indexed sets efficiently with a
    'construction dictionary' pattern: on the first call, make a single
    traversal through all H2 production projects to generate a complete index,
    use that for subsequent lookups, and clean up at the last call."""

    def PROD_IN_ZONE_init(m, z):
        if not hasattr(m, 'PROD_IN_ZONE_dict'):
            m.PROD_IN_ZONE_dict = {_z: [] for _z in m.LOAD_ZONES}
            for h in m.PRODUCTION_PROJECTS:
                m.PROD_IN_ZONE_dict[m.prod_load_zone[h]].append(h)
        result = m.PROD_IN_ZONE_dict.pop(z)
        if not m.PROD_IN_ZONE_dict:
            del m.PROD_IN_ZONE_dict
        return result
    m.PROD_IN_ZONE = Set(
        m.LOAD_ZONES,
        initialize=PROD_IN_ZONE_init
    )

    m.CAPACITY_LIMITED_PROD = Set(within=m.PRODUCTION_PROJECTS)
    m.prod_capacity_limit_mw = Param(
        m.CAPACITY_LIMITED_PROD, input_file="h2_production_projects_info.csv",
        input_optional=True, within=NonNegativeReals)
    
    m.prod_leakage_rate = Param(
        m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
        default=0, within=NonNegativeReals)

    m.prod_ccs_equipped = Param(
        m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
        input_optional=True, within=Boolean)

    m.prod_is_onsite = Param(
        m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
        input_optional=True, within=Boolean)
    m.prod_onsite_GENERATION_PROJECT = Param(
        m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
        input_optional=True, within=m.GENERATION_PROJECTS)
    m.prod_onsite_gen_tech = Param(
        m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
        input_optional=True, within=m.gen_tech)
    m.ONSITE_PRODUCTION_PROJECTS = Set(within=m.PRODUCTION_PROJECTS)
    m.GRID_CONNECTED_PRODUCTION_PROJECTS = Set(
		within=m.PRODUCTION_PROJECTS,
		initialize=lambda m: m.PRODUCTION_PROJECTS - m.ONSITE_PRODUCTION_PROJECTS
	)
    m.ONSITE_PROD_AND_GEN = Set(
		dimen=2,
		within=m.PRODUCTION_PROJECTS * m.GENERATION_PROJECTS,
		initialize=lambda m: [
			(h, m.prod_onsite_GENERATION_PROJECT[h])
			for h in m.ONSITE_PRODUCTION_PROJECTS
		]
	)

    m.prod_uses_fuel = Param(
        m.PRODUCTION_PROJECTS,
        initialize=lambda m, h: (
            m.prod_energy_source[h] in m.FUELS))
    m.FUEL_BASED_PROD = Set(
        initialize=m.PRODUCTION_PROJECTS,
        filter=lambda m, h: m.prod_uses_fuel[h])

    m.mmbtu_fuel_per_kg_h2 = Param(m.FUEL_BASED_PROD, input_file="h2_production_projects_info.csv",
                                          within=NonNegativeReals, default=0)

    m.mwh_per_kg_h2 = Param(m.PRODUCTION_TECHNOLOGIES, input_file="h2_production_projects_info.csv",
                                          within=NonNegativeReals, default=0)

    m.FUEL_FOR_PROD = Set(m.FUEL_BASED_PROD,
        initialize=lambda m, h: [m.prod_energy_source[h]])

    def PROD_BY_ENERGY_SOURCE_init(m, e):
        if not hasattr(m, 'PROD_BY_ENERGY_dict'):
            m.PROD_BY_ENERGY_dict = {_e: [] for _e in m.ENERGY_SOURCES}
            for h in m.PRODUCTION_PROJECTS:
                if h in m.FUEL_BASED_PROD:
                    for f in m.FUEL_FOR_PROD[h]:
                        m.PROD_BY_ENERGY_dict[f].append(h)
                else:
                    m.PROD_BY_ENERGY_dict[m.prod_energy_source[h]].append(h)
        result = m.PROD_BY_ENERGY_dict.pop(e)
        if not m.PROD_BY_ENERGY_dict:
            del m.PROD_BY_ENERGY_dict
        return result
    m.PROD_BY_ENERGY_SOURCE = Set(
        m.ENERGY_SOURCES,
        initialize=PROD_BY_ENERGY_SOURCE_init
    )
    m.PROD_BY_NON_FUEL_ENERGY_SOURCE = Set(
        m.NON_FUEL_ENERGY_SOURCES,
        initialize=lambda m, s: m.PROD_BY_ENERGY_SOURCE[s]
    )
    m.PROD_BY_FUEL = Set(
        m.FUELS,
        initialize=lambda m, f: m.PROD_BY_ENERGY_SOURCE[f]
    )

    # This set is defined by h2_prod_predetermined.csv
    m.PREDETERMINED_PROD_BLD_YRS = Set(
        input_file="h2_prod_predetermined.csv",
        input_optional=True,
        dimen=2)
    m.PREDETERMINED_BLD_YRS_FOR_PROD = Set(
        dimen=1,
        ordered=False,
        initialize=lambda m: set(bld_yr for (h, bld_yr) in m.PREDETERMINED_PROD_BLD_YRS),
        doc="Set of all the years where pre-determined builds for H2 production projects occur."
    )

    # This set is defined by h2_prod_build_costs.csv
    m.PROD_BLD_YRS = Set(
        dimen=2,
        input_file="h2_prod_build_costs.csv",
        validate=lambda m, h, bld_yr: (
            (h, bld_yr) in m.PREDETERMINED_PROD_BLD_YRS or
            (h, bld_yr) in m.PRODUCTION_PROJECTS * m.PERIODS))
    m.NEW_PROD_BLD_YRS = Set(
        dimen=2,
        initialize=lambda m: m.PROD_BLD_YRS - m.PREDETERMINED_PROD_BLD_YRS)
    m.prod_predetermined_cap_mw = Param(
        m.PREDETERMINED_PROD_BLD_YRS,
        input_file="h2_prod_predetermined.csv",
        within=NonNegativeReals)

    def prod_build_can_operate_in_period(m, h, build_year, p):
        # If a period has the same name as a predetermined build year then we have a problem.
        # For example, consider what happens if we have both a period named 2020
        # and a predetermined build in 2020. In this case, "build_year in m.PERIODS"
        # will be True even if the project is a 2020 predetermined build.
        # This will result in the "online" variable being the start of the period rather
        # than the prebuild year which can cause issues such as the project retiring too soon.
        # To prevent this we've added the prod_no_predetermined_bld_yr_vs_period_conflict BuildCheck below.
        if build_year in m.PERIODS:
            online = m.period_start[build_year]
        else:
            online = build_year
        retirement = online + m.prod_max_age[h]
        # Previously the code read return online <= m.period_start[p] < retirement
        # However using the midpoint of the period as the "cutoff" seems more correct so
        # we've made the switch.
        return online <= m.period_start[p] + 0.5 * m.period_length_years[p] < retirement

    # This verifies that a predetermined build year doesn't conflict with a period since if that's the case
    # prod_build_can_operate_in_period will mistaken the prebuild for an investment build
    # (see note in prod_build_can_operate_in_period)
    m.prod_no_predetermined_bld_yr_vs_period_conflict = BuildCheck(
        m.PREDETERMINED_BLD_YRS_FOR_PROD, m.PERIODS,
        rule=lambda m, bld_yr, p: bld_yr != p
    )

    # The set of periods when a project built in a certain year will be online
    m.PERIODS_FOR_PROD_BLD_YR = Set(
        m.PROD_BLD_YRS,
        within=m.PERIODS,
        ordered=True,
        initialize=lambda m, h, bld_yr: [
            p for p in m.PERIODS
            if prod_build_can_operate_in_period(m, h, bld_yr, p)])

    m.BLD_YRS_FOR_PROD = Set(
        m.PRODUCTION_PROJECTS,
        ordered=False,
        initialize=lambda m, h: set(
            bld_yr for (prod, bld_yr) in m.PROD_BLD_YRS if prod == h
        )
    )

    # The set of build years that could be online in the given period
    # for the given H2 production project.
    m.BLD_YRS_FOR_PROD_PERIOD = Set(
        m.PRODUCTION_PROJECTS, m.PERIODS,
        ordered=False,
        initialize=lambda m, h, p: set(
            bld_yr for bld_yr in m.BLD_YRS_FOR_PROD[h]
            if prod_build_can_operate_in_period(m, h, bld_yr, p)))
    # The set of periods when a h2 production plant is available to run
    m.PERIODS_FOR_PROD = Set(
        m.PRODUCTION_PROJECTS,
        initialize=lambda m, h: [p for p in m.PERIODS if len(m.BLD_YRS_FOR_PROD_PERIOD[h, p]) > 0]
    )

    def bounds_BuildProd(model, h, bld_yr):
        if((h, bld_yr) in model.PREDETERMINED_PROD_BLD_YRS):
            return (model.prod_predetermined_cap_mw[h, bld_yr],
                    model.prod_predetermined_cap_mw[h, bld_yr])
        elif(h in model.CAPACITY_LIMITED_PROD):
            # This does not replace Max_Prod_Build_Potential because
            # Max_Prod_Build_Potential applies across all build years.
            return (0, model.prod_capacity_limit_mw[h])
        else:
            return (0, None)
    m.BuildProd = Var(
        m.PROD_BLD_YRS,
        within=NonNegativeReals,
        bounds=bounds_BuildProd)

    # Some projects are retired before the first study period, so they
    # don't appear in the objective function or any constraints.
    # In this case, pyomo may leave the variable value undefined even
    # after a solve, instead of assigning a value within the allowed
    # range. This causes errors in the Progressive Hedging code, which
    # expects every variable to have a value after the solve. So as a
    # starting point we assign an appropriate value to all the existing
    # projects here.
    m.BuildProd_assign_default_value = BuildAction(
        m.PREDETERMINED_PROD_BLD_YRS,
        rule=get_assign_default_value_rule("BuildProd", "prod_predetermined_cap_mw"))

    m.PROD_PERIODS = Set(
        dimen=2,
        initialize=lambda m:
            [(h, p) for h in m.PRODUCTION_PROJECTS for p in m.PERIODS_FOR_PROD[h]])

    m.ProdCapacity = Expression(
        m.PRODUCTION_PROJECTS, m.PERIODS,
        rule=lambda m, h, p: sum(
            m.BuildProd[h, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_PROD_PERIOD[h, p]))

    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    # Note we removed the ability to specify a minumum build capacity 
    # for H2 production projects as to avoid binary variables
    max_build_potential_scaling_factor = 1e-1
    m.Max_Prod_Build_Potential = Constraint(
        m.CAPACITY_LIMITED_PROD, m.PERIODS,
        rule=lambda m, h, p: (
                m.prod_capacity_limit_mw[h] * max_build_potential_scaling_factor >= m.ProdCapacity[
            h, p] * max_build_potential_scaling_factor))

    # Costs
    m.prod_variable_om_per_kg = Param(m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
                                within=NonNegativeReals)
    m.min_data_check('prod_variable_om_per_kg')

    m.prod_overnight_cost_per_mw = Param(
        m.PROD_BLD_YRS,
        input_file="h2_prod_build_costs.csv",
        within=NonNegativeReals)
    m.prod_fixed_om_per_mw_yr = Param(
        m.PROD_BLD_YRS,
        input_file="h2_prod_build_costs.csv",
        within=NonNegativeReals)
    m.min_data_check('prod_overnight_cost_per_mw', 'prod_fixed_om_per_mw_yr')

    # Derived annual costs
    m.prod_capital_cost_annual = Param(
        m.PROD_BLD_YRS,
        initialize=lambda m, h, bld_yr: (
            m.prod_overnight_cost_per_mw[h, bld_yr] *
            crf(m.interest_rate, m.prod_max_age[h])))

    m.ProdCapitalCosts = Expression(
        m.PRODUCTION_PROJECTS, m.PERIODS,
        rule=lambda m, h, p: sum(
            m.BuildProd[h, bld_yr] * m.prod_capital_cost_annual[h, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_PROD_PERIOD[h, p]))
    m.ProdFixedOMCosts = Expression(
        m.PRODUCTION_PROJECTS, m.PERIODS,
        rule=lambda m, h, p: sum(
            m.BuildProd[h, bld_yr] * m.prod_fixed_om_per_mw_yr[h, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_PROD_PERIOD[h, p]))
    # Summarize costs for the objective function. Units should be total
    # annual future costs in $base_year real dollars. The objective
    # function will convert these to base_year Net Present Value in
    # $base_year real dollars.
    m.TotalProdFixedCosts = Expression(
        m.PERIODS,
        rule=lambda m, p: sum(
            m.ProdCapitalCosts[h, p] + m.ProdFixedOMCosts[h, p]
            for h in m.PRODUCTION_PROJECTS))
    m.Cost_Components_Per_Period.append('TotalProdFixedCosts')
    
    ### DISPATCH ###

    def period_active_prod_rule(m, period):
        if not hasattr(m, 'period_active_prod_dict'):
            m.period_active_prod_dict = collections.defaultdict(set)
            for (_h, _period) in m.PROD_PERIODS:
                m.period_active_prod_dict[_period].add(_h)
        result = m.period_active_prod_dict.pop(period)
        if len(m.period_active_prod_dict) == 0:
            delattr(m, 'period_active_prod_dict')
        return result
    m.PROD_IN_PERIOD = Set(m.PERIODS, ordered=False, initialize=period_active_prod_rule,
        doc="The set of H2 production projects active in a given period.")

    m.TPS_FOR_PROD = Set(
        m.PRODUCTION_PROJECTS,
        within=m.TIMEPOINTS,
        initialize=lambda m, h: (
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
    m.TPS_FOR_PROD_IN_PERIOD = Set(
        m.PRODUCTION_PROJECTS, m.PERIODS,
        ordered=False,
        within=m.TIMEPOINTS, initialize=init)

    m.PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.PRODUCTION_PROJECTS
                    for tp in m.TPS_FOR_PROD[h]))
    m.ONSITE_PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.ONSITE_PRODUCTION_PROJECTS
                    for tp in m.TPS_FOR_PROD[h]))
    m.ONSITE_PROD_GEN_TPS = Set(
        dimen=3,
        initialize=lambda m: (
            (h, g, tp)
                for (h, g) in m.ONSITE_PROD_AND_GEN
                    for tp in m.TPS_FOR_PROD[h]))
    m.GRID_CONNECTED_PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.GRID_CONNECTED_PRODUCTION_PROJECTS
                    for tp in m.TPS_FOR_PROD[h]))
    m.FUEL_BASED_PROD_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (h, tp)
                for h in m.FUEL_BASED_PROD
                    for tp in m.TPS_FOR_PROD[h]))
    m.PROD_TP_FUELS = Set(
        dimen=3,
        initialize=lambda m: (
            (h, t, f)
                for (h, t) in m.FUEL_BASED_PROD_TPS
                    for f in m.FUEL_FOR_PROD[h]))
    m.PROD_TP_PROD_TECH = Set(
        dimen=3,
        initialize=lambda m: (
            (h, t, tech)
                for (h, t) in m.FUEL_BASED_PROD_TPS
                    for tech in m.prod_tech[h]))

    m.ProdCapacityInTP = Expression(
        m.PROD_TPS,
        rule=lambda m, h, t: m.ProdCapacity[h, m.tp_period[t]])
    m.DispatchProd = Var(
        m.PROD_TPS,
        within=NonNegativeReals)

    # Define DispatchProdByFuel to simply equal the total dispatch (no multi-fuel capabilities).
    m.DispatchProdByFuel = Expression(
        m.PROD_TP_FUELS,
        rule=lambda m, h, t, f: m.DispatchProd[h, t]
    )

    # Only used to improve the performance of calculating ZoneTotalCentralH2Dispatch and H2ProdGridCntdPowerZonalUse
    m.PROD_FOR_ZONE_TPS = Set(
        m.LOAD_ZONES, m.TIMEPOINTS,
        ordered=False,
        initialize=lambda m, z, t: set(h for h in m.PROD_IN_ZONE[z] if (h, t) in m.PROD_TPS)
    )
    m.GRID_CONNECTED_PROD_FOR_ZONE_TPS = Set(
        m.LOAD_ZONES, m.TIMEPOINTS,
        ordered=False,
        initialize=lambda m, z, t: set(h for h in m.PROD_IN_ZONE[z] if (h, t) in m.GRID_CONNECTED_PROD_TPS)
    )

    m.ZoneTotalCentralH2Dispatch = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: \
        sum(m.DispatchProd[h, t] * (1- m.prod_leakage_rate[h])
            for h in m.PROD_FOR_ZONE_TPS[z, t]),
        doc="Total H2 from H2 production projects per zone at each timepoint in MW of H2.")
    m.Zone_H2_Injections.append('ZoneTotalCentralH2Dispatch')
    
    m.H2_Production_Zonal_H2_Leakage = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: \
        sum(m.DispatchProd[h, t] * m.prod_leakage_rate[h]
            for h in m.PROD_FOR_ZONE_TPS[z, t]),
        doc="Total H2 leakage from H2 production projects per zone at each timepoint in MW of H2.")
    # Annual leakage of H2 (fugitive H2 emissions) in each period
	# Units: [MW of H2] * [hours] * [1 kg of H2/33.32 kWh] * [1000 kWh/1 MWh] * [1 metric ton/1000 kg] = [metric ton of H2]
	# 1000/1000 cancels, hence (1/33.32)
    m.H2ProductionTotalLeakage = Expression(
        m.PERIODS, 
        rule=lambda m,p: sum(
			m.H2_Production_Zonal_H2_Leakage[z, t] * m.tp_weight_in_year[t] * (1/33.32)
			for z in m.LOAD_ZONES for t in m.TPS_IN_PERIOD[p]
		)
    )
    m.Period_Fugitive_H2.append('H2ProductionTotalLeakage')

    def init_prod_availability(m, h):
        return (1 - m.prod_av_outage_rate[h])
    m.prod_availability = Param(
        m.PRODUCTION_PROJECTS,
        within=NonNegativeReals,
        initialize=init_prod_availability)
    # Units: [MW of H2] * [MWh of power/kg of H2] * [1000 kWh/MWh] * [1 kg of H2/33.32 kWh] = [MW of power]
    m.H2ProdGridCntdPowerUse = Expression(
        m.GRID_CONNECTED_PROD_TPS,
        rule=lambda m, h, t: \
        m.DispatchProd[h, t] * m.mwh_per_kg_h2[h] * (1000/33.32),
        doc=("[MW] Average power used at each TP by grid-powered/grid-connected hydrogen production plants."))
    m.H2ProdGridCntdPowerZonalUse = Expression(
        m.LOAD_ZONES, m.TIMEPOINTS,
        rule=lambda m, z, t: \
            sum(m.H2ProdGridCntdPowerUse[h, t] for h in m.GRID_CONNECTED_PROD_FOR_ZONE_TPS[z, t]),
        doc=("[MW] Average power used at each TP by grid-powered/grid-connected hydrogen production plants in each zone."))
    m.Zone_Power_Withdrawals.append("H2ProdGridCntdPowerZonalUse")

    m.ProdFuelUseRate = Var(
        m.PROD_TP_FUELS,
        within=NonNegativeReals,
        doc=("[MMBTU/h] Other modules constrain this variable based on DispatchProdByFuel."))

    # -- LOAD EMISSIONS PARAMETERS AND CO2 POLICY --
    # GREENHOUSE GASES (LHV of H2 = 33.32 kWh/kg)
    m.kg_co2_per_kg_h2 = Param(m.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		input_file="h2_emissions_factors.csv", input_column="kg_co2_per_kg_h2")
    m.kg_ch4_per_kg_h2 = Param(m.PRODUCTION_TECHNOLOGIES, within=Reals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_ch4_per_kg_h2")
    m.kg_n2o_per_kg_h2 = Param(m.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_n2o_per_kg_h2")
    
    m.h2_carbon_cap_tco2_per_yr = Param(m.PERIODS, within=NonNegativeReals,
		input_file="h2_carbon_policies.csv", input_column="h2_carbon_cap_tco2_per_yr")
    m.ch4_gwp = Param(m.PERIODS, within=NonNegativeReals,
		input_file="h2_carbon_policies.csv", input_column="ch4_gwp")
    m.n2o_gwp = Param(m.PERIODS, within=NonNegativeReals,
		input_file="h2_carbon_policies.csv", input_column="n2o_gwp")
    m.h2_gwp = Param(m.PERIODS, within=NonNegativeReals,
		input_file="h2_carbon_policies.csv", input_column="h2_gwp")
	 
	# CRITERIA AIR POLLUTANTS (LHV of H2 = 33.32 kWh/kg)
    m.kg_so2_per_kg_h2 = Param(m.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_so2_per_kg_h2")
    m.kg_nox_per_kg_h2 = Param(m.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_nox_per_kg_h2")
    m.kg_pm10_per_kg_h2 = Param(m.PRODUCTION_TECHNOLOGIES, within=NonNegativeReals,
		default=0, input_file="h2_emissions_factors.csv", input_column="kg_pm10_per_kg_h2")
	
	# -- EMISSIONS EXPRESSIONS PER TP (metric tonnes = kg * 1e-3) [metric tonnes per hour] --
	# GREENHOUSE GASES
    def ProdDispatchEmissions_rule_co2(m, h, t, f):
        return (m.ProdFuelUseRate[h, t, f] * (1 / m.mmbtu_fuel_per_kg_h2[h]) * m.kg_co2_per_kg_h2[m.prod_tech[h]] * 1e-3)
    m.ProdDispatchEmissionsCO2 = Expression(m.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_co2)
	
    def ProdDispatchEmissions_rule_ch4(m, h, t, f):
        return (m.ProdFuelUseRate[h, t, f] * (1 / m.mmbtu_fuel_per_kg_h2[h]) * m.kg_ch4_per_kg_h2[m.prod_tech[h]] * 1e-3)
    m.ProdDispatchEmissionsCH4 = Expression(m.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_ch4)
	
    def ProdDispatchEmissions_rule_n2o(m, h, t, f):
        return (m.ProdFuelUseRate[h, t, f] * (1 / m.mmbtu_fuel_per_kg_h2[h]) * m.kg_n2o_per_kg_h2[m.prod_tech[h]] * 1e-3)
    m.ProdDispatchEmissionsN2O = Expression(m.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_n2o)

	# CRITERIA AIR POLLUTANTS
    def ProdDispatchEmissions_rule_so2(m, h, t, f):
        return (m.ProdFuelUseRate[h, t, f] * (1 / m.mmbtu_fuel_per_kg_h2[h]) * m.kg_so2_per_kg_h2[m.prod_tech[h]] * 1e-3)
    m.ProdDispatchEmissionsSO2 = Expression(m.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_so2)
	
    def ProdDispatchEmissions_rule_nox(m, h, t, f):
        return (m.ProdFuelUseRate[h, t, f] * (1 / m.mmbtu_fuel_per_kg_h2[h]) * m.kg_nox_per_kg_h2[m.prod_tech[h]] * 1e-3)
    m.ProdDispatchEmissionsNOx = Expression(m.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_nox)
	
    def ProdDispatchEmissions_rule_pm10(m, h, t, f):
        return (m.ProdFuelUseRate[h, t, f] * (1 / m.mmbtu_fuel_per_kg_h2[h]) * m.kg_pm10_per_kg_h2[m.prod_tech[h]] * 1e-3)
    m.ProdDispatchEmissionsPM10 = Expression(m.PROD_TP_FUELS, rule=ProdDispatchEmissions_rule_pm10)

	# -- ANNUAL TOTALS[metric tonnes per year] --
	# GREENHOUSE GASES
    m.ProdAnnualEmissionsCO2 = Expression(m.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsCO2[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual CO2 emissions, in metric tonnes per year.")

    m.ProdAnnualEmissionsCH4 = Expression(m.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsCH4[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual CH4 emissions, in metric tonnes per year.")

    m.ProdAnnualEmissionsN2O = Expression(m.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsN2O[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual N2O emissions, in metric tonnes per year.")
    
    m.ProdAnnualEmissionsCO2equivalent = Expression(m.PERIODS,
		rule=lambda m, p: sum(
			m.ProdAnnualEmissionsCO2[p] + 
			m.ch4_gwp[p] * m.ProdAnnualEmissionsCH4[p] + 
			m.n2o_gwp[p] * m.ProdAnnualEmissionsN2O[p] + 
			m.h2_gwp[p] * m.System_Fugitive_H2[p]),
		doc="The system's annual CO2 equivalent (sum of GHGs with GWP coefficients) emissions, in metric tonnes per year.")
    
    ## Carbon constraint ##
    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    enforce_h2carbon_cap_scaling_factor = 1e-1
    m.Enforce_H2_Carbon_Cap = Constraint(m.PERIODS,
                                           rule=lambda m, p:
                                           Constraint.Skip if m.h2_carbon_cap_tco2_per_yr[p] == float('inf')
                                           else m.ProdAnnualEmissionsCO2equivalent[p] * enforce_h2carbon_cap_scaling_factor <=
                                                m.h2_carbon_cap_tco2_per_yr[p]
                                                * enforce_h2carbon_cap_scaling_factor,
                                           doc=("Enforces the carbon cap for hydrogen-related CO2 direct + equivalent emissions)."))

    # CRITERIA AIR POLLUTANTS
    m.ProdAnnualEmissionsSO2 = Expression(m.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsSO2[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual SO2 emissions, in metric tonnes per year.")

    m.ProdAnnualEmissionsNOx = Expression(m.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsNOx[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual NOx emissions, in metric tonnes per year.")

    m.ProdAnnualEmissionsPM10 = Expression(m.PERIODS,
		rule=lambda m, period: sum(
			m.ProdDispatchEmissionsPM10[h, t, f] * m.tp_weight_in_year[t]
			for (h, t, f) in m.PROD_TP_FUELS
			if m.tp_period[t] == period),
		doc="The system's annual PM10 emissions, in metric tonnes per year.")

	# DispatchProd tells us the average production in units of MW of H2 produced in each timepoint. 
	# This value is multiplied by the duration of the timepoint in hours to determine the amount of 
	# H2 produced by a project over the duration of timepoint t in units of MWh. This is converted 
	# to kg of H2 using the LHV of H2 (33.32 kWh/kg) and the conversion factor of 1000 kWh/MWh.

    m.H2ProdVariableOMCostsInPeriod = Expression(
		m.PERIODS,
		rule=lambda m, p: sum(
			m.DispatchProd[h, t] * m.tp_weight_in_year[t] * (1000 / 33.32) * m.prod_variable_om_per_kg[h]
			for t in m.TPS_IN_PERIOD[p]
			for h in m.PROD_IN_PERIOD[m.tp_period[t]]
		),
		doc="Summarize variable OM costs per kg of H2 produced in each period for the objective function"
	)
    m.Cost_Components_Per_Period.append('H2ProdVariableOMCostsInPeriod')

    m.ProdDispatchUpperLimit = Expression(
        m.PROD_TPS,
        rule=lambda m, h, t: m.ProdCapacityInTP[h, t] * m.prod_availability[h])

    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    m.Enforce_Prod_Dispatch_Upper_Limit = Constraint(
        m.PROD_TPS,
        rule=lambda m, h, t:
        m.DispatchProd[h, t] * 1e4 <= 1e4 * m.ProdDispatchUpperLimit[h, t]
    )
	# [MW of H2] * [MMBtu / kg of H2] * [kg of H2 /33.32 kWh] * [1000 kWh / MWh] = MMBTU / h
    m.ProdFuelUseRate_Calculate = Constraint(
        m.PROD_TP_FUELS,
        rule=lambda m, h, t, f: m.ProdFuelUseRate[h, t, f] == m.DispatchProdByFuel[h, t, f] * m.mmbtu_fuel_per_kg_h2[h] * (1000 / 33.32)
    )
    
    ### BALANCING ###
    
    m.zone_demand_mw_h2 = Param(
        m.ZONE_TIMEPOINTS,
        input_file="h2_demand.csv",
        within=NonNegativeReals)
    m.Zone_H2_Withdrawals.append('zone_demand_mw_h2')

def load_inputs(m, switch_data, inputs_dir):
    # Construct set of capacity-limited projects. This set includes projects for 
    # which the prod_capacity_limit_mw parameter has a value.
    # Note we removed the capability to have discretely sized H2 production techs
    if 'prod_capacity_limit_mw' in switch_data.data():
        switch_data.data()['CAPACITY_LIMITED_PROD'] = {
            None: list(switch_data.data(name='prod_capacity_limit_mw').keys())}
    
    # Construct set of H2 production projects that are installed onsite of a power
    # generator. This set includes projects for which the parameter prod_is_onsite is True.
    if 'prod_is_onsite' in switch_data.data():
        onsite_projects = [
            h for h, is_onsite in switch_data.data(name='prod_is_onsite').items()
            if is_onsite
        ]
        switch_data.data()['ONSITE_PRODUCTION_PROJECTS'] = {None: onsite_projects}

def post_solve(m, outdir):
    """
    Exported files:
    
    h2_prod_cap.csv - Capacity build out and cost results by production project.

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
    write_table(
        m,
        m.PROD_PERIODS,
        output_file=os.path.join(outdir, "h2_prod_cap.csv"),
        headings=(
            "PRODUCTION_PROJECT", "PERIOD",
            "prod_tech", "prod_load_zone", "prod_energy_source",
            "ProdCapacity", "ProdCapitalCosts", "ProdFixedOMCosts"),
        # Indexes are provided as a tuple, so put (h, p) in parentheses to
        # access the two components of the index individually.
        values=lambda m, h, p: (
            h, p,
            m.prod_tech[h], m.prod_load_zone[h], m.prod_energy_source[h],
            m.ProdCapacity[h, p], m.ProdCapitalCosts[h, p], m.ProdFixedOMCosts[h, p]))
    
    sorted_prod = sorted_robust(m.PRODUCTION_PROJECTS)
    write_table(
        m, m.TIMEPOINTS,
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
        return (value(func(h, t)) for h, t in m.PROD_TPS)

    # Note we've refactored to create the Dataframe in one
    # line to reduce the overall memory consumption during
    # the most intensive part of post-solve (this function)
    prod_dispatch_full_df = pd.DataFrame({
        "production_project": c(lambda h, t: h),
        "prod_tech": c(lambda h, t: m.prod_tech[h]),
        "prod_load_zone": c(lambda h, t: m.prod_load_zone[h]),
        "prod_energy_source": c(lambda h, t: m.prod_energy_source[h]),
        "timestamp": c(lambda h, t: m.tp_timestamp[t]),
        "tp_weight_in_year_hrs": c(lambda h, t: m.tp_weight_in_year[t]),
        "period": c(lambda h, t: m.tp_period[t]),
        "DispatchProd_MW_H2": c(lambda h, t: m.DispatchProd[h, t]),
        "Curtailment_MW_H2": c(lambda h, t:
                            value(m.ProdDispatchUpperLimit[h, t]) - value(m.DispatchProd[h, t])),
        "H2_produced_tonne_typical_yr": c(lambda h, t:
                                   m.DispatchProd[h, t] * m.tp_weight_in_year[t] / 33.32),
        "ProdVariableOMCost_per_yr": c(lambda h, t:
                                   m.DispatchProd[h, t] * m.tp_weight_in_year[t] * (1000 / 33.32) *
                                   m.prod_variable_om_per_kg[h]),
        "ProdDispatchEmissions_tCO2_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       m.ProdDispatchEmissionsCO2[h, t, f] *
                                                       m.tp_weight_in_year[t]
                                                       for f in m.FUEL_FOR_PROD[h]
                                                   ) if m.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tCH4_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       m.ProdDispatchEmissionsCH4[h, t, f] *
                                                       m.tp_weight_in_year[t]
                                                       for f in m.FUEL_FOR_PROD[h]
                                                   ) if m.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tN2O_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       m.ProdDispatchEmissionsN2O[h, t, f] *
                                                       m.tp_weight_in_year[t]
                                                       for f in m.FUEL_FOR_PROD[h]
                                                   ) if m.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tSO2_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       m.ProdDispatchEmissionsSO2[h, t, f] *
                                                       m.tp_weight_in_year[t]
                                                       for f in m.FUEL_FOR_PROD[h]
                                                   ) if m.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tNOx_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       m.ProdDispatchEmissionsNOx[h, t, f] *
                                                       m.tp_weight_in_year[t]
                                                       for f in m.FUEL_FOR_PROD[h]
                                                   ) if m.prod_uses_fuel[h] else 0),
        "ProdDispatchEmissions_tPM10_per_typical_yr": c(lambda h, t:
                                                   sum(
                                                       m.ProdDispatchEmissionsPM10[h, t, f] *
                                                       m.tp_weight_in_year[t]
                                                       for f in m.FUEL_FOR_PROD[h]
                                                   ) if m.prod_uses_fuel[h] else 0)
    })
    prod_dispatch_full_df.set_index(["production_project", "timestamp"], inplace=True)
    write_table(m, output_file=os.path.join(outdir, "h2_prod_dispatch.csv"), df=prod_dispatch_full_df)

    prod_annual_summary = prod_dispatch_full_df.groupby(['prod_tech', "prod_energy_source", "period"]).sum()
    write_table(m, output_file=os.path.join(outdir, "h2_prod_dispatch_annual_summary.csv"),
                df=prod_annual_summary,
                columns=["H2_produced_tonne_typical_yr", "ProdVariableOMCost_per_yr",
                         "ProdDispatchEmissions_tCO2_per_typical_yr", "ProdDispatchEmissions_tCH4_per_typical_yr",
                         "ProdDispatchEmissions_tN2O_per_typical_yr", "ProdDispatchEmissions_tSO2_per_typical_yr",
                         "ProdDispatchEmissions_tNOx_per_typical_yr", "ProdDispatchEmissions_tPM10_per_typical_yr"])

    prod_zonal_annual_summary = prod_dispatch_full_df.groupby(
        ['prod_tech', "prod_load_zone", "prod_energy_source", "period"]
    ).sum()
    write_table(
        m,
        output_file=os.path.join(outdir, "h2_prod_dispatch_zonal_annual_summary.csv"),
        df=prod_zonal_annual_summary,
        columns=["H2_produced_tonne_typical_yr", "ProdVariableOMCost_per_yr",
                 "ProdDispatchEmissions_tCO2_per_typical_yr", "ProdDispatchEmissions_tCH4_per_typical_yr",
                 "ProdDispatchEmissions_tN2O_per_typical_yr", "ProdDispatchEmissions_tSO2_per_typical_yr",
                 "ProdDispatchEmissions_tNOx_per_typical_yr", "ProdDispatchEmissions_tPM10_per_typical_yr"]
    )
    
    """
    The following code exports h2_balance.csv, h2_balance_annual_zonal.csv, and h2_balance_annual.csv.
    Each component registered with Zone_H2_Injections and Zone_H2_Withdrawals will
    become a column in these .csv files. As such, each column represents an H2 injection
    or withdrawal and the sum of across all columns should be zero. Note that positive
    terms are net injections (e.g. generation) while negative terms are net withdrawals
    (e.g. load).

    h2_balance.csv contains the energy balance terms for for every zone and timepoint.
    We also include a column called normalized_h2_balance_duals_dollar_per_mwh
    that is a proxy for the locational marginal pricing (LMP). This value represents
    the incremental cost per hour to increase the H2 demand by 1 MW of H2 (or equivalently
    the incremental cost of providing one more MWh of H2). This is not a perfect
    proxy for LMP since it factors in build costs etc.

    h2_balance_annual_zonal.csv contains the H2 injections and withdrawals
    throughout a year for a given load zone.

    h2_balance_annual.csv contains the H2 injections and withdrawals
    throughout a year across all zones.
    """
    write_table(
        m, m.LOAD_ZONES, m.TIMEPOINTS,
        output_file=os.path.join(outdir, "h2_balance.csv"),
        headings=("load_zone", "timestamp", "normalized_h2_balance_duals_dollar_per_mwh",) + tuple(
            m.Zone_H2_Injections +
            m.Zone_H2_Withdrawals),
        values=lambda m, z, t:
        (
            z,
            m.tp_timestamp[t],
            m.get_dual(
                "Zone_H2_Balance",
                z, t,
                divider=m.bring_timepoint_costs_to_base_year[t]
            )
        )
        + tuple(getattr(m, component)[z, t] for component in m.Zone_H2_Injections)
        + tuple(-getattr(m, component)[z, t] for component in m.Zone_H2_Withdrawals)
    )

    def get_component_per_year(m, z, p, component):
        """
        Returns the weighted sum of component across all timepoints in the given period.
        The components must be indexed by zone and timepoint.
        """
        return sum(getattr(m, component)[z, t] * m.tp_weight_in_year[t] for t in m.TPS_IN_PERIOD[p])

    write_table(
        m, m.LOAD_ZONES, m.PERIODS,
        output_file=os.path.join(outdir, "h2_balance_annual_zonal.csv"),
        headings=("load_zone", "period",) + tuple(m.Zone_H2_Injections + m.Zone_H2_Withdrawals),
        values=lambda m, z, p:
        (z, p)
        + tuple(get_component_per_year(m, z, p, component) for component in m.Zone_H2_Injections)
        + tuple(-get_component_per_year(m, z, p, component) for component in m.Zone_H2_Withdrawals)
    )

    write_table(
        m, m.PERIODS,
        output_file=os.path.join(outdir, "h2_balance_annual.csv"),
        headings=("period",) + tuple(m.Zone_H2_Injections + m.Zone_H2_Withdrawals),
        values=lambda m, p:
        (p,)
        + tuple(sum(get_component_per_year(m, z, p, component) for z in m.LOAD_ZONES)
                for component in m.Zone_H2_Injections)
        + tuple(-sum(get_component_per_year(m, z, p, component) for z in m.LOAD_ZONES)
                for component in m.Zone_H2_Withdrawals)
    )