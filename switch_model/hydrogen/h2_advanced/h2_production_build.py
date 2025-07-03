"""
Defines hydrogen (H2) production projects build-outs for advanced hydrogen model.

INPUT FILE FORMAT
    Import data describing project builds. The following files are
    expected in the input directory.

    h2_production_projects_info.csv has mandatory and optional columns. 
    You may drop optional columns entirely or mark blank
    values with a dot '.' for select rows for which the column does not
    apply. Mandatory columns are:
        PRODUCTION_PROJECT, prod_tech, prod_energy_source, prod_load_zone,
        prod_max_age, prod_variable_om_per_kg
    Optional columns are:
        h2_color, prod_av_outage_rate, prod_capacity_limit_mw, 
        prod_ccs_energy_load, prod_ccs_capture_efficiency

    The following file lists existing builds of H2 production projects, and is
    optional for simulations where there is no existing capacity:

    h2_prod_build_predetermined.csv
        PRODUCTION_PROJECT, build_year, prod_predetermined_cap_mw

    The following file is mandatory, because it sets cost parameters for
    both existing and new project buildouts:

    h2_prod_build_costs.csv
        PRODUCTION_PROJECT, build_year, prod_overnight_cost_per_mw, prod_fixed_om_per_mw
"""

from __future__ import division
import os
from pyomo.environ import *
from switch_model.financials import capital_recovery_factor as crf
from switch_model.reporting import write_table
from switch_model.utilities.scaling import get_assign_default_value_rule

dependencies = 'switch_model.timescales', 'switch_model.balancing.load_zones',\
    'switch_model.financials', 'switch_model.energy_sources.properties.properties'

def define_components(m):
    if not m.options.no_hydrogen:
        define_hydrogen_components(m)

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
    	
    *Note: Electrolyzers are rated in MW, where MW is the max power input that the electrolyzer can take. 
    Fuel-based H2 production technologies are also rated by MW in this model, but MW refers to MW of hydrogen, 
    which is a measure of its maximum production capacity in kg of H2 per hour, converted to MW using the LHV 
    of H2 of 33.3 kWh/kg from https://www.engineeringtoolbox.com/fuels-higher-calorific-values-d_169.html
    (Example: Say an SMR plant is rated for 300 kg of H2 per hour. Then we have:
    300 kg_H2/hr * 33.3 kWh/kg * 1 MW/1,000 kW = 9.99 MW of H2)
    
    ------------------------------------------
    
    PRODUCTION_PROJECTS is the set of H2 production projects that
    have been built or could potentially be built. A project is a combination
    of H2 production technology, load zone and location. A particular build-out
    of a project should also include the year in which construction was
    complete and additional capacity came online. Members of this set are
    abbreviated as prod in parameter names and h in indexes (think of the mnemonic: h for H2).
    Use of p instead of g is discouraged because p is reserved for period.

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

    prod_capacity_limit_mw[h] is defined for H2 production technologies that are
    capacity limited. This describes the maximum possible capacity of an H2 
    production project in units of megawatts or megawatts of H2. See the *note 
    above for more information on the capacity units.

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
    electricity for electrolyzers or MW of H2 for fuel-based H2 production projects.

    BuildProd[h, build_year] is a decision variable that describes
    how much capacity of a project to install in a given period. This also
    stores the amount of capacity that was installed in existing projects
    that are still online.

    ProdCapacity[h, period] is an expression that returns the total
    capacity online in a given period. This is the sum of installed capacity
    minus all retirements.

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

    --- COSTS ---

	The following cost components are defined for each project and build
    year. These parameters will always be available, but will typically
    be populated by the generic costs specified in h2_prod_build_costs.csv
    input file.

    prod_overnight_cost_per_mw[h, build_year] is the overnight capital cost per
    MW of capacity for building a project in the given period. By
    "installed in the given period", I mean that it comes online at the
    beginning of the given period and construction starts before that.

    prod_fixed_om_per_mw[h, build_year] is the annual fixed Operations and
    Maintenance costs (O&M) per MW of capacity for given project that
    was installed in the given period.

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

    m.h2_color = Param(m.PRODUCTION_PROJECTS, input_file="h2_production_projects_info.csv",
                            input_optional=True, within=Strs)

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
                m.PROD_IN_ZONE_dict[m.prod_load_zone[h]].append(p)
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
    
    m.CCS_EQUIPPED_PROD Set(within=m.PRODUCTION_PROJECTS)
    m.prod_ccs_capture_efficiency = Param(
        m.CCS_EQUIPPED_PROD, input_file="h2_production_projects_info.csv",
        input_optional=True, within=PercentFraction)
    m.prod_ccs_energy_load = Param(
        m.CCS_EQUIPPED_PROD, input_file="h2_production_projects_info.csv",
        input_optional=True, within=PercentFraction)

    m.prod_uses_fuel = Param(
        m.PRODUCTION_PROJECTS,
        initialize=lambda m, h: (
            m.prod_energy_source[h] in m.FUELS
                or m.prod_energy_source[h] == "multiple"))
    m.NON_FUEL_BASED_PROD = Set(
        initialize=m.PRODUCTION_PROJECTS,
        filter=lambda m, h: not m.prod_uses_fuel[h])
    m.FUEL_BASED_PROD = Set(
        initialize=m.PRODUCTION_PROJECTS,
        filter=lambda m, h: m.prod_uses_fuel[h])
    
    #default value of 3.5 based on SMR, from Q6 in https://seshydrogen.com/en/frequently-asked-questions-about-hydrogen-3/
    m.kg_h2_per_kg_fuel = Param(m.FUEL_BASED_PROD, input_file="h2_production_projects_info.csv",
                                          within=NonNegativeReals, default=3.5)

    m.kg_h2_per_mwh = Param(m.NON_FUEL_BASED_PROD, input_file="h2_production_projects_info.csv",
                                          within=NonNegativeReals, default=30)

    m.FUELS_FOR_PROD = Set(m.FUEL_BASED_PROD,
        initialize=lambda m, h: [m.prod_energy_source[h]])

    def PROD_BY_ENERGY_SOURCE_init(m, e):
        if not hasattr(m, 'PROD_BY_ENERGY_dict'):
            m.PROD_BY_ENERGY_dict = {_e: [] for _e in m.ENERGY_SOURCES}
            for h in m.PRODUCTION_PROJECTS:
                if p in m.FUEL_BASED_PROD:
                    for f in m.FUELS_FOR_PROD[h]:
                        m.PROD_BY_ENERGY_dict[f].append(p)
                else:
                    m.PROD_BY_ENERGY_dict[m.prod_energy_source[h]].append(p)
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
        # To prevent this we've added the no_predetermined_bld_yr_vs_period_conflict BuildCheck below.
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
        elif(p in model.CAPACITY_LIMITED_PROD):
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
    m.prod_fixed_om_per_mw = Param(
        m.PROD_BLD_YRS,
        input_file="h2_prod_build_costs.csv",
        within=NonNegativeReals)
    m.min_data_check('prod_overnight_cost_per_mw', 'prod_fixed_om_per_mw')

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
            m.BuildProd[h, bld_yr] * m.prod_fixed_om_per_mw[h, bld_yr]
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

def load_inputs(m, switch_data, inputs_dir):
    # Construct sets of capacity-limited and ccs-capable projects. 
    # These sets include projects for which these parameters have a value.
    # Note we removed the capability to have discretely sized H2 production techs
    if 'prod_capacity_limit_mw' in switch_data.data():
        switch_data.data()['CAPACITY_LIMITED_PROD'] = {
            None: list(switch_data.data(name='prod_capacity_limit_mw').keys())}
    if 'prod_ccs_capture_efficiency' in switch_data.data():
        switch_data.data()['CCS_EQUIPPED_PROD'] = {
            None: list(switch_data.data(name='prod_ccs_capture_efficiency').keys())}

def post_solve(m, outdir):
    write_table(
        m,
        m.PROD_PERIODS,
        output_file=os.path.join(outdir, "h2_prod_cap.csv"),
        headings=(
            "PRODUCTION_PROJECT", "PERIOD",
            "prod_tech", "prod_load_zone", "prod_energy_source", "h2_color",
            "ProdCapacity", "ProdCapitalCosts", "ProdFixedOMCosts"),
        # Indexes are provided as a tuple, so put (h, p) in parentheses to
        # access the two components of the index individually.
        values=lambda m, h, p: (
            h, p,
            m.prod_tech[h], m.prod_load_zone[h], m.prod_energy_source[h], m.h2_color[h]
            m.ProdCapacity[h, p], m.ProdCapitalCosts[h, p], m.ProdFixedOMCosts[h, p]))