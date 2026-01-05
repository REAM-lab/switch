# Copyright (c) 2015-2019 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
Defines model components to describe H2-fueled electricity generation project build-outs for the Switch model. 
Technology options are H2 fuel cells, H2 combustion turbines, and H2 combined cycle turbines.

INPUT FILE FORMAT
    Import project-specific data from an input directory.
    
    h2_to_power_projects_info.csv:
    This input file imports the technology parameters and costs for electricity generation projects that use
    hydrogen fuel. This module enables endogenous hydrogen consumption for balancing power generation and 
    hydrogen production. This module is optional when running the advanced hydrogen model, but necessary when 
    investigating long-duration energy storage from hydrogen in your research.
    
    h2_to_power_projects_info.csv
        H2_GENERATION_PROJECT, h2gen_build_yr, h2gen_tech, h2gen_load_zone, h2gen_max_age, 
        h2gen_full_load_heat_rate, h2gen_connect_cost_per_mw,  h2gen_variable_om_per_mwh, 
        h2gen_capacity_limit_mw, h2gen_forced_outage_rate, h2gen_can_provide_cap_reserves, 
        mt_nox_per_mmbtu_h2, h2gen_leakage_rate

    h2_to_power_predetermined.csv
        H2_GENERATION_PROJECT, build_year, h2gen_predetermined_cap_mw

    h2_to_power_build_costs.csv
        H2_GENERATION_PROJECT, build_year, h2gen_overnight_cost_per_mw_per_mw, h2gen_fixed_om_cost_per_mw_yr

"""
from __future__ import division

import os, collections
import pandas as pd

from pyomo.common.sorting import sorted_robust
from pyomo.environ import *


from switch_model.reporting import write_table
from switch_model.financials import capital_recovery_factor as crf
from switch_model.utilities.scaling import get_assign_default_value_rule

dependencies = 'switch_model.timescales', 'switch_model.balancing.load_zones', \
               'switch_model.financials', 'switch_model.hydrogen.advanced.h2_production_dispatch'

def define_components(mod):
    """
    Adds components to a Pyomo abstract model object to describe
    hydrogen-fueled generation projects. Unless otherwise stated, all power
    capacity is specified in units of MW and all sets and parameters
    are mandatory.

    H2_GENERATION_PROJECTS is the set of generation and storage projects that
    have been built or could potentially be built. A project is a combination
    of generation technology, load zone and location. A particular build-out
    of a project should also include the year in which construction was
    complete and additional capacity came online. Members of this set are
    abbreviated as gen in parameter names and g in indexes. Use of p instead
    of g is discouraged because p is reserved for period.

    h2gen_tech[g] describes what kind of technology a hydrogen-fueled generation 
    project is using.

    h2gen_load_zone[g] is the load zone this hydrogen-fueled generation project 
    is built in.

    H2_GENS_IN_ZONE[z in LOAD_ZONES] is an indexed set that lists all
    hydrogen-fueled generation projects within each load zone.

    CAPACITY_LIMITED_H2_GENS is the subset of H2_GENERATION_PROJECTS and 
    bld_yr that are capacity limited. Some existing or proposed hydrogen-fueled
    generation projects may have upper bounds on increasing capacity or replacing 
    capacity as it is retired based on permits or local air quality regulations.

    h2gen_capacity_limit_mw[g, bld_yr] is defined for generation technologies 
    that are resource limited and do not compete for land area. This describes 
    the maximum possible capacity of a generation project in units of megawatts.
    Each limit only applies to each corresponding build year. The total limit for 
    the project should equal the sum of all the capacity limits over all build 
    years for each project.

    -- CONSTRUCTION --

    H2_GEN_BLD_YRS is a two-dimensional set of hydrogen-fueled generation 
    projects and the years in which construction or expansion occured or 
    can occur. You can think of a project as a physical site that can be 
    built out over time. h2gen_bld_yr is the year in which construction is 
    completed and new capacity comes online, not the year when constrution 
    begins. h2gen_bld_yr will be in the past for existing projects and will 
    be the first year of an investment period for new projects. Investment
    decisions are made for each project/invest period combination. This
    set is derived from other parameters for all new construction. This
    set also includes entries for existing projects that have already
    been built and planned projects whose capacity buildouts have already been
    decided; information for legacy projects come from other files
    and their build years will usually not correspond to the set of
    investment periods. There are two recommended options for
    abbreviating this set for denoting indexes: typically this should be
    written out as (g, build_year) for clarity, but when brevity is
    more important (g, b) is acceptable.

    PREDETERMINED_H2_GEN_BLD_YRS is a subset of H2_GEN_BLD_YRS that
    only includes existing or planned projects that are not subject to
    optimization.

    h2gen_predetermined_cap_mw[(g, build_year) in PREDETERMINED_H2_GEN_BLD_YRS] is
    a parameter that describes how much capacity was built in the past
    for existing projects, or is planned to be built for future projects.

    BuildH2Gen[g, build_year] is a decision variable that describes
    how much capacity of a project to install in a given period. This also
    stores the amount of capacity that was installed in existing projects
    that are still online.

    H2GenCapacity[g, period] is an expression that returns the total
    capacity online in a given period. This is the sum of installed capacity
    minus all retirements.

    --- OPERATIONS ---

    PERIODS_FOR_H2_GEN_BLD_YR[g, build_year] is an indexed
    set that describes which periods a given project build will be
    operational.

    BLD_YRS_FOR_H2_GEN_PERIOD[g, period] is a complementary
    indexed set that identify which build years will still be online
    for the given project in the given period. For some project-period
    combinations, this will be an empty set.

    H2_GEN_PERIODS describes periods in which hydrogen-fueled generation 
    projects could be operational. Unlike the related sets above, it 
    is not indexed. Instead it is specified as a set of (g, period)
    combinations useful for indexing other model components.

    --- COSTS ---

    h2gen_connect_cost_per_mw[g] is the cost of grid upgrades to support a
    new project, in dollars per peak MW. These costs include new
    transmission lines to a substation, substation upgrades and any
    other grid upgrades that are needed to deliver power from the
    interconnect point to the load center or from the load center to the
    broader transmission network.

    The following cost components are defined for each project and build
    year. These parameters will always be available, but will typically
    be populated by the generic costs specified in generator costs
    inputs file and the load zone cost adjustment multipliers from
    load_zones inputs file.

    h2gen_overnight_cost_per_mw[g, build_year] is the overnight capital cost per
    MW of capacity for building a project in the given period. By
    "installed in the given period", I mean that it comes online at the
    beginning of the given period and construction starts before that.

    h2gen_fixed_om_cost_per_mw_yr[g, build_year] is the annual fixed Operations and
    Maintenance costs (O&M) per MW of capacity for given project that
    was installed in the given period.

    -- Derived cost parameters --

    h2gen_capital_cost_annual[g, build_year] is the annualized loan
    payments for a project's capital and connection costs in units of
    $/MW per year. This is specified in non-discounted real dollars in a
    future period, not real dollars in net present value.

    H2GenProj_Fixed_Costs_Annual[g, period] is the total annual fixed
    costs (capital as well as fixed operations & maintenance) incurred
    by a project in a period. This reflects all of the builds are
    operational in the given period. This is an expression that reflect
    decision variables.=00

    ProjFixedCosts[period] is the sum of
    H2GenProj_Fixed_Costs_Annual[g, period] for all projects that could be
    online in the target period. This aggregation is performed for the
    benefit of the objective function.

    """
    # inputs from h2_to_power_projects_info.csv
    mod.H2_GENERATION_PROJECTS = Set(dimen=1, input_file="h2_to_power_projects_info.csv")
    mod.h2gen_tech = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                           within=Any)
    mod.H2_GENERATION_TECHNOLOGIES = Set(ordered=False, initialize=lambda m:
                                         {m.h2gen_tech[g] for g in m.H2_GENERATION_PROJECTS})
    mod.h2gen_load_zone = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                                within=mod.LOAD_ZONES)
    mod.h2gen_max_age = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                              within=PositiveIntegers)
    mod.h2gen_full_load_heat_rate = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                                          within=NonNegativeReals) 
    mod.h2gen_forced_outage_rate = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                                         within=PercentFraction, default=0)
    mod.h2gen_can_provide_cap_reserves = Param(mod.H2_GENERATION_PROJECTS, input_file='generation_projects_info.csv',
                                             within=Boolean, default=True, 
                                             doc="Indicates whether an H2-fueled generator can provide capacity reserves.")
    mod.mt_nox_per_mmbtu_h2 = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                                         within=NonNegativeReals, default=0)
    mod.h2gen_leakage_rate = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                                         within=NonNegativeReals, default=0)
    mod.min_data_check('H2_GENERATION_PROJECTS', 'h2gen_tech', 'h2gen_load_zone', 'h2gen_max_age')

    """Construct H2_GENS_* indexed sets efficiently with a
    'construction dictionary' pattern: on the first call, make a single
    traversal through all hydrogen-fueled generation projects to generate a complete index,
    use that for subsequent lookups, and clean up at the last call."""

    def H2_GENS_IN_ZONE_init(m, z):
        if not hasattr(m, 'H2_GENS_IN_ZONE_dict'):
            m.H2_GENS_IN_ZONE_dict = {_z: [] for _z in m.LOAD_ZONES}
            for g in m.H2_GENERATION_PROJECTS:
                m.H2_GENS_IN_ZONE_dict[m.h2gen_load_zone[g]].append(g)
        result = m.H2_GENS_IN_ZONE_dict.pop(z)
        if not m.H2_GENS_IN_ZONE_dict:
            del m.H2_GENS_IN_ZONE_dict
        return result
    mod.H2_GENS_IN_ZONE = Set(
        mod.LOAD_ZONES,
        initialize=H2_GENS_IN_ZONE_init
    )
    
    mod.CAPACITY_LIMITED_H2_GENS = Set(within=mod.H2_GENERATION_PROJECTS)
    mod.h2gen_capacity_limit_mw = Param(
        mod.CAPACITY_LIMITED_H2_GENS, input_file="h2_to_power_projects_info.csv",
        input_optional=True, within=NonNegativeReals)
    
    # inputs from h2_to_power_predetermined.csv
    mod.PREDETERMINED_H2_GEN_BLD_YRS = Set(
	    input_file="h2_to_power_predetermined.csv",
        input_optional=True,
        dimen=2)
    mod.PREDETERMINED_BLD_YRS_FOR_H2_GEN = Set(
        dimen=1,
        ordered=False,
        initialize=lambda m: set(bld_yr for (g, bld_yr) in m.PREDETERMINED_H2_GEN_BLD_YRS),
        doc="Set of all the years where pre-determined builds occurs for H2 to power generators."
    )
    mod.h2gen_predetermined_cap_mw = Param(
        mod.PREDETERMINED_H2_GEN_BLD_YRS,
        input_file="h2_to_power_predetermined.csv",
        within=NonNegativeReals)
    mod.min_data_check('h2gen_predetermined_cap_mw')

    # inputs from by h2_to_power_build_costs.csv
    mod.H2_GEN_BLD_YRS = Set(
        dimen=2,
        input_file="h2_to_power_build_costs.csv",
        validate=lambda m, g, bld_yr: (
            (g, bld_yr) in m.PREDETERMINED_H2_GEN_BLD_YRS or
            (g, bld_yr) in m.H2_GENERATION_PROJECTS * m.PERIODS))

    def h2gen_build_can_operate_in_period(m, g, build_year, period):
        # If a period has the same name as a predetermined build year then we have a problem.
        # For example, consider what happens if we have both a period named 2020
        # and a predetermined build in 2020. In this case, "build_year in m.PERIODS"
        # will be True even if the project is a 2020 predetermined build.
        # This will result in the "online" variable being the start of the period rather
        # than the prebuild year which can cause issues such as the project retiring too soon.
        # To prevent this we've added the h2gen_no_predetermined_bld_yr_vs_period_conflict BuildCheck below.
        if build_year in m.PERIODS:
            online = m.period_start[build_year]
        else:
            online = build_year
        retirement = online + m.h2gen_max_age[g]
        # Previously the code read return online <= m.period_start[period] < retirement
        # However using the midpoint of the period as the "cutoff" seems more correct so
        # we've made the switch.
        return online <= m.period_start[period] + 0.5 * m.period_length_years[period] < retirement

    # This verifies that a predetermined build year doesn't conflict with a period since if that's the case
    # h2gen_build_can_operate_in_period will mistaken the prebuild for an investment build
    # (see note in h2gen_build_can_operate_in_period)
    mod.h2gen_no_predetermined_bld_yr_vs_period_conflict = BuildCheck(
        mod.PREDETERMINED_BLD_YRS_FOR_H2_GEN, mod.PERIODS,
        rule=lambda m, bld_yr, p: bld_yr != p
    )

    # The set of periods when a project built in a certain year will be online
    mod.PERIODS_FOR_H2_GEN_BLD_YR = Set(
        mod.H2_GEN_BLD_YRS,
        within=mod.PERIODS,
        ordered=True,
        initialize=lambda m, g, bld_yr: [
            period for period in m.PERIODS
            if h2gen_build_can_operate_in_period(m, g, bld_yr, period)])

    mod.BLD_YRS_FOR_H2_GEN = Set(
        mod.H2_GENERATION_PROJECTS,
        ordered=False,
        initialize=lambda m, g: set(
            bld_yr for (gen, bld_yr) in m.H2_GEN_BLD_YRS if gen == g
        )
    )

    # The set of build years that could be online in the given period
    # for the given project.
    mod.BLD_YRS_FOR_H2_GEN_PERIOD = Set(
        mod.H2_GENERATION_PROJECTS, mod.PERIODS,
        ordered=False,
        initialize=lambda m, g, period: set(
            bld_yr for bld_yr in m.BLD_YRS_FOR_H2_GEN[g]
            if h2gen_build_can_operate_in_period(m, g, bld_yr, period)))
    # The set of periods when a generator is available to run
    mod.PERIODS_FOR_H2_GEN = Set(
        mod.H2_GENERATION_PROJECTS,
        initialize=lambda m, g: [p for p in m.PERIODS if len(m.BLD_YRS_FOR_H2_GEN_PERIOD[g, p]) > 0]
    )

    def bounds_BuildH2Gen(mod, g, bld_yr):
        if((g, bld_yr) in mod.PREDETERMINED_H2_GEN_BLD_YRS):
            return (mod.h2gen_predetermined_cap_mw[g, bld_yr],
                    mod.h2gen_predetermined_cap_mw[g, bld_yr])
        elif(g in mod.CAPACITY_LIMITED_H2_GENS):
            # This does not replace Max_H2Gen_Build_Potential because
            # Max_H2Gen_Build_Potential applies across all build years.
            return (0, mod.h2gen_capacity_limit_mw[g])
        else:
            return (0, None)
    mod.BuildH2Gen = Var(
        mod.H2_GEN_BLD_YRS,
        within=NonNegativeReals,
        bounds=bounds_BuildH2Gen)
    # Some projects are retired before the first study period, so they
    # don't appear in the objective function or any constraints.
    # In this case, pyomo may leave the variable value undefined even
    # after a solve, instead of assigning a value within the allowed
    # range. This causes errors in the Progressive Hedging code, which
    # expects every variable to have a value after the solve. So as a
    # starting point we assign an appropriate value to all the existing
    # projects here.
    mod.BuildH2Gen_assign_default_value = BuildAction(
        mod.PREDETERMINED_H2_GEN_BLD_YRS,
        rule=get_assign_default_value_rule("BuildH2Gen", "h2gen_predetermined_cap_mw"))

    # note: in pull request 78, commit e7f870d..., H2_GEN_PERIODS
    # was mistakenly redefined as H2_GENERATION_PROJECTS * PERIODS.
    # That didn't directly affect the objective function in the tests
    # because most code uses GEN_TPS, which was defined correctly.
    # But it did have some subtle effects on the main Hawaii model.
    # It would be good to have a test that this set is correct,
    # e.g., assertions that in the 3zone_toy model,
    # ('C-Coal_ST', 2020) in m.H2_GEN_PERIODS and ('C-Coal_ST', 2030) not in m.H2_GEN_PERIODS
    # and 'C-Coal_ST' in m.GENS_IN_PERIOD[2020] and 'C-Coal_ST' not in m.GENS_IN_PERIOD[2030]
    mod.H2_GEN_PERIODS = Set(
        dimen=2,
        initialize=lambda m:
            [(g, p) for g in m.H2_GENERATION_PROJECTS for p in m.PERIODS_FOR_H2_GEN[g]])

    mod.H2GenCapacity = Expression(
        mod.H2_GENERATION_PROJECTS, mod.PERIODS,
        rule=lambda m, g, period: sum(
            m.BuildH2Gen[g, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_H2_GEN_PERIOD[g, period]))
    
    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    max_build_potential_scaling_factor = 1e-1
    mod.Max_H2_Gen_Build_Potential = Constraint(
        mod.CAPACITY_LIMITED_H2_GENS, mod.PERIODS,
        rule=lambda m, s, p: (
                m.h2gen_capacity_limit_mw[s] * max_build_potential_scaling_factor >= 
                m.H2GenCapacity[s, p] * max_build_potential_scaling_factor))

    ### Costs ###
    mod.h2gen_connect_cost_per_mw = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                                          within=NonNegativeReals)
    mod.h2gen_variable_om_per_mwh = Param(mod.H2_GENERATION_PROJECTS, input_file="h2_to_power_projects_info.csv",
                                          within=NonNegativeReals)   

    mod.h2gen_overnight_cost_per_mw = Param(mod.H2_GEN_BLD_YRS, input_file="h2_to_power_build_costs.csv",
                                          within=NonNegativeReals)
    mod.h2gen_fixed_om_cost_per_mw_yr = Param(mod.H2_GEN_BLD_YRS, input_file="h2_to_power_build_costs.csv",
                                          within=NonNegativeReals)
    
    mod.min_data_check('h2gen_connect_cost_per_mw', 'h2gen_overnight_cost_per_mw', 'h2gen_fixed_om_cost_per_mw_yr', 'h2gen_variable_om_per_mwh')

    # Derived annual costs
    mod.h2gen_capital_cost_annual = Param(
        mod.H2_GEN_BLD_YRS,
        initialize=lambda m, g, bld_yr: (
            (m.h2gen_overnight_cost_per_mw[g, bld_yr] +
                m.h2gen_connect_cost_per_mw[g]) *
            crf(m.interest_rate, m.h2gen_max_age[g])))

    mod.H2GenCapitalCosts = Expression(
        mod.H2_GENERATION_PROJECTS, mod.PERIODS,
        rule=lambda m, g, p: sum(
            m.BuildH2Gen[g, bld_yr] * m.h2gen_capital_cost_annual[g, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_H2_GEN_PERIOD[g, p]))
    mod.H2GenFixedOMCosts = Expression(
        mod.H2_GENERATION_PROJECTS, mod.PERIODS,
        rule=lambda m, g, p: sum(
            m.BuildH2Gen[g, bld_yr] * m.h2gen_fixed_om_cost_per_mw_yr[g, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_H2_GEN_PERIOD[g, p]))
    # Summarize costs for the objective function. Units should be total
    # annual future costs in $base_year real dollars. The objective
    # function will convert these to base_year Net Present Value in
    # $base_year real dollars.
    mod.TotalH2GenFixedCosts = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            m.H2GenCapitalCosts[g, p] + m.H2GenFixedOMCosts[g, p]
            for g in m.H2_GENERATION_PROJECTS))
    mod.Cost_Components_Per_Period.append('TotalH2GenFixedCosts')
    
    ### DISPATCH ###
    def period_active_h2gen_rule(m, period):
        if not hasattr(m, 'period_active_h2gen_dict'):
            m.period_active_h2gen_dict = collections.defaultdict(set)
            for (_g, _period) in m.H2_GEN_PERIODS:
                m.period_active_h2gen_dict[_period].add(_g)
        result = m.period_active_h2gen_dict.pop(period)
        if len(m.period_active_h2gen_dict) == 0:
            delattr(m, 'period_active_h2gen_dict')
        return result
    mod.H2_GENS_IN_PERIOD = Set(mod.PERIODS, ordered=False, initialize=period_active_h2gen_rule,
        doc="The set of projects active in a given period.")

    mod.TPS_FOR_H2_GEN = Set(
        mod.H2_GENERATION_PROJECTS,
        within=mod.TIMEPOINTS,
        initialize=lambda m, g: (
            tp for p in m.PERIODS_FOR_H2_GEN[g] for tp in m.TPS_IN_PERIOD[p]
        )
    )

    def init(m, gen, period):
        try:
            d = m._TPS_FOR_H2_GEN_IN_PERIOD_dict
        except AttributeError:
            d = m._TPS_FOR_H2_GEN_IN_PERIOD_dict = dict()
            for _gen in m.H2_GENERATION_PROJECTS:
                for t in m.TPS_FOR_H2_GEN[_gen]:
                    d.setdefault((_gen, m.tp_period[t]), set()).add(t)
        result = d.pop((gen, period), set())
        if not d:  # all gone, delete the attribute
            del m._TPS_FOR_H2_GEN_IN_PERIOD_dict
        return result
    mod.TPS_FOR_H2_GEN_IN_PERIOD = Set(
        mod.H2_GENERATION_PROJECTS, mod.PERIODS,
        ordered=False,
        within=mod.TIMEPOINTS, initialize=init)

    mod.H2_GEN_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (g, tp)
                for g in m.H2_GENERATION_PROJECTS
                    for tp in m.TPS_FOR_H2_GEN[g]))

    mod.h2gen_availability = Param(
        mod.H2_GENERATION_PROJECTS,
        within=NonNegativeReals,
        rule=lambda m, g: (1 - m.h2gen_forced_outage_rate[g])
    )
    mod.H2GenCapacityInTP = Expression(
        mod.H2_GEN_TPS,
        rule=lambda m, g, t: m.H2GenCapacity[g, m.tp_period[t]]
    )
    mod.DispatchH2Gen = Var(
        mod.H2_GEN_TPS,
        within=NonNegativeReals)
    mod.H2GenDispatchUpperLimit = Expression(
        mod.H2_GEN_TPS,
        rule=lambda m, g, t: m.H2GenCapacityInTP[g, t] * m.h2gen_availability[g]
    )
    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    mod.Enforce_H2Gen_Dispatch_Upper_Limit = Constraint(
        mod.H2_GEN_TPS,
        rule=lambda m, g, t:
        m.DispatchH2Gen[g, t] * 1e4 <= 1e4 * m.H2GenDispatchUpperLimit[g, t]
    )
    # Only used to improve the performance of calculating ZoneTotalH2GenDispatch
    mod.H2_GENS_FOR_ZONE_TPS = Set(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        ordered=False,
        initialize=lambda m, z, t: set(g for g in m.H2_GENS_IN_ZONE[z] if (g, t) in m.H2_GEN_TPS)
    )
    mod.ZoneTotalH2GenDispatch = Expression(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        rule=lambda m, z, t: \
        sum(m.DispatchH2Gen[g, t] for g in m.H2_GENS_FOR_ZONE_TPS[z, t]),
    doc="Total power from hydrogen-fueled electricity generation projects.")
    mod.Zone_Power_Injections.append('ZoneTotalH2GenDispatch')

    # Units: [MW of power generated] * [MMBtu of H2 consumed/MWh of electricity generated] * [0.293071 MWh/MMBtu] = [MW of H2 consumed]
    # [MMBtu/MWh is the full load heat rate of the generator]
    # [0.293071 MWh/MMBtu] is simply a conversion factor
    mod.ZoneTotalH2GeneratorH2Use = Expression(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        rule=lambda m, z, t: sum(m.DispatchH2Gen[g, t] * m.h2gen_full_load_heat_rate[g] * 0.293071 for g in m.H2_GENS_FOR_ZONE_TPS[z, t]),
        doc="Total H2 consumed from H2-fueled electricity generators per zone at each timepoint in MW of H2.")
    mod.Zone_H2_Withdrawals.append('ZoneTotalH2GeneratorH2Use')

    # Keep track of annual fugitive H2 emissions in each part of the H2 system 
    # in metric tons of kg per zone from each timepoint
    # Units: [MW of H2] * [hours] * [1 kg of H2/33.32 kWh] * [1000 kWh/1 MWh] * [1 metric ton/1000 kg] * [frac of H2 leaked] = [metric ton of H2]
	# 1000/1000 cancels, hence (1/33.32)
    mod.H2GeneratorTotalLeakage_ZoneTP = Expression(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        rule=lambda m, z, t: sum(
            m.ZoneTotalH2GeneratorH2Use[z, t] *
            m.h2gen_leakage_rate[g] * m.tp_weight_in_year[t] * (1/33.32)
			for g in m.H2_GENS_FOR_ZONE_TPS[z,t])
    )
    # Annual per period
    mod.H2GeneratorTotalAnnualLeakage = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            m.H2GeneratorTotalLeakage_ZoneTP[z, t]
			for z in m.LOAD_ZONES for t in m.TPS_IN_PERIOD[p]
            )
    )
    mod.Period_Fugitive_H2.append("H2GeneratorTotalAnnualLeakage")

    mod.H2GenVariableOMCostsInTP = Expression(
        mod.TIMEPOINTS,
        rule=lambda m, t: sum(
            m.DispatchH2Gen[g, t] * m.h2gen_variable_om_per_mwh[g]
            for g in m.H2_GENS_IN_PERIOD[m.tp_period[t]]),
        doc="Summarize H2-fueled generator variable O&M costs for the objective function")
    mod.Cost_Components_Per_TP.append('H2GenVariableOMCostsInTP')
                 
    mod.H2GenAnnualNOxEmissions = Expression(
        mod.PERIODS,
        rule=lambda m, period: sum(
            m.DispatchH2Gen[g, t] * m.mt_nox_per_mmbtu_h2[g] * m.tp_weight_in_year[t]
            for (g, t) in m.H2_GEN_TPS
            if m.tp_period[t] == period),
        doc="The system's annual NOx emissions from H2-fueled electricity generators in metric tonnes of NOx per year.")


def load_inputs(mod, switch_data, inputs_dir):
    # Construct set of capacity-limited projects. This set includes projects for which 
    # the parameter has a value
    if 'h2gen_capacity_limit_mw' in switch_data.data():
        switch_data.data()['CAPACITY_LIMITED_H2_GENS'] = {
            None: list(switch_data.data(name='h2gen_capacity_limit_mw').keys())}

def post_solve(m, outdir):
    """
    Exported files: (all files are for H2-fueled generators only)
    
    h2gen_cap.csv - Installed capacity and fixed cost results by 
    H2_GENERATION_PROJECT and PERIOD

    h2gen-dispatch.csv - Dispatch results in normalized form where each row
    describes the dispatch of a generation project in one timepoint.

    h2gen-dispatch_annual_summary.csv - Similar to dispatch.csv, but summarized
    by generation technology and period.

    h2gen-dispatch_zonal_annual_summary.csv - Similar to dispatch_annual_summary.csv
    but broken out by load zone.
    
    """
    write_table(
        m, m.H2_GEN_PERIODS,
        output_file=os.path.join(outdir, "h2gen_cap.csv"),
        headings=(
            "H2_GENERATION_PROJECT", "PERIOD",
            "h2gen_tech", "h2gen_load_zone", "H2GenCapacity", 
            "H2GenCapitalCosts", "H2GenFixedOMCosts"),
        # Indexes are provided as a tuple, so put (g,p) in parentheses to
        # access the two components of the index individually.
        values=lambda m, g, p: (
            g, p,
            m.h2gen_tech[g], m.h2gen_load_zone[g], m.H2GenCapacity[g, p], 
            m.H2GenCapitalCosts[g, p], m.H2GenFixedOMCosts[g, p]))

    def c(func):
        return (value(func(g, t)) for g, t in m.H2_GEN_TPS)

    # Note we've refactored to create the Dataframe in one
    # line to reduce the overall memory consumption during
    # the most intensive part of post-solve (this function)
    h2gen_dispatch_full_df = pd.DataFrame({
        "h2_generation_project": c(lambda g, t: g),
        "h2gen_tech": c(lambda g, t: m.h2gen_tech[g]),
        "h2gen_load_zone": c(lambda g, t: m.h2gen_load_zone[g]),
        "h2gen_energy_source": "hydrogen",
        "timestamp": c(lambda g, t: m.tp_timestamp[t]),
        "tp_weight_in_year_hrs": c(lambda g, t: m.tp_weight_in_year[t]),
        "period": c(lambda g, t: m.tp_period[t]),
        "DispatchH2Gen_MW": c(lambda g, t: m.DispatchH2Gen[g, t]),
        "Curtailment_MW": c(lambda g, t:
                            value(m.H2GenDispatchUpperLimit[g, t]) - value(m.DispatchH2Gen[g, t])),
        "Energy_GWh_typical_yr": c(lambda g, t:
                                   m.DispatchH2Gen[g, t] * m.tp_weight_in_year[t] / 1000),
        "VariableOMCost_per_yr": c(lambda g, t:
                                   m.DispatchH2Gen[g, t] * m.h2gen_variable_om_per_mwh[g] *
                                   m.tp_weight_in_year[t]),
        "DispatchEmissions_tNOx": c(lambda g, t: # Units: [MW] * [h] * [MMBtu of H2/MWh] * [metric ton NOx/MMBtu of H2] = [metric ton NOx]
                                                   m.DispatchH2Gen[g, t] 
                                                   * m.tp_weight_in_year[t]
                                                   * m.h2gen_full_load_heat_rate[g]
                                                   * m.mt_nox_per_mmbtu_h2[g])
    })
    h2gen_dispatch_full_df.set_index(["h2_generation_project", "timestamp"], inplace=True)
    write_table(m, output_file=os.path.join(outdir, "h2gen_dispatch.csv"), df=h2gen_dispatch_full_df)

    h2gen_annual_summary = h2gen_dispatch_full_df.groupby(['h2gen_tech', "h2gen_energy_source", "period"]).sum()
    write_table(m, output_file=os.path.join(outdir, "h2gen_dispatch_annual_summary.csv"),
                df=h2gen_annual_summary,
                columns=["Energy_GWh_typical_yr", "VariableOMCost_per_yr",
                         "DispatchEmissions_tNOx"])

    h2gen_zonal_annual_summary = h2gen_dispatch_full_df.groupby(
        ['h2gen_tech', "h2gen_load_zone", "h2gen_energy_source", "period"]
    ).sum()
    write_table(
        m,
        output_file=os.path.join(outdir, "h2gen_dispatch_zonal_annual_summary.csv"),
        df=h2gen_zonal_annual_summary,
        columns=["Energy_GWh_typical_yr", "VariableOMCost_per_yr",
                 "DispatchEmissions_tNOx"]
    )