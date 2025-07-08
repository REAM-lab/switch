# Copyright (c) 2015-2019 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
Defines hydrogen transport (pipelines) build-outs.

INPUT FILE FORMAT
    Import data related to H2 pipeline builds. The following files are
    expected in the input directory:

    h2_pipelines.csv
        PIPELINE, pip_lz1, pip_lz2, pip_length_km, pip_efficiency,
        existing_pip_cap_mw, pip_terrain_multiplier
    The last column of h2_pipelines.csv is optional. If the
    column is missing or if cells contain a dot (.), pip_terrain_multiplier
    will be set to default value as described in documentation.

    Note that in the next file, parameter names are written on the first
    row (as usual), and the single value for each parameter is written in
    the second row.

    h2_pipeline_params.csv
        pip_capital_cost_per_mw_km, pip_capital_cost_y_int_per_km, 
        pip_lifetime_yrs, pip_fixed_om_pc
"""

import os
from pyomo.environ import *
from switch_model.financials import capital_recovery_factor as crf
import pandas as pd
from switch_model.reporting import write_table

dependencies = 'switch_model.timescales', \
    'switch_model.financials','switch_model.balancing.load_zones',

def define_components(mod):
    if not mod.options.no_hydrogen:
        define_hydrogen_components(mod)

def define_hydrogen_components(mod):
    """

    Adds components to a Pyomo abstract model object to describe bulk
    H2 pipelines of an H2 system. This includes parameters, build
    decisions and constraints. Unless otherwise stated, all pipeline
    capacity is specified in units of MW of hydrogen and all sets and parameters 
    are mandatory. MW of hydrogen is a measure of the pipeline's maximum hydrogen 
    flow in kg of H2 per s, converted to MW using the LHV 
    of H2 of 33.32 kWh/kg from https://www.engineeringtoolbox.com/fuels-higher-calorific-values-d_169.html
    and https://sci-hub.kvnp.top/10.1016/j.ijhydene.2019.10.080. 
    (Example: Say a pipeline is rated for 8.34 kg of H2 per s. Then we have:
    8.34 kg_H2/s * 33.32 kWh/kg * 1 MW/1,000 kW * 3,600 s/hr =~ 1,000 MW of H2 or 1 GW of H2)

    PIPELINES is the complete set of pipeline pathways
    connecting load zones. Each member of this set is a one dimensional
    identifier such as "A-B". This set has no regard for directionality
    of pipelines and will generate an error if you specify two
    lines that move in opposite directions such as (A to B) and (B to
    A). Another derived set - PIPELINES_DIRECTIONAL - stores
    directional information. Pipelines may be abbreviated as trans or
    pip in parameter names or indexes.

    pip_lz1[pip] and pip_lz2[pip] specify the load zones at either end
    of a pipeline. The order of 1 and 2 is unimportant, but you
    are encouraged to be consistent to simplify merging information back
    into external databases.

    pip_length_km[pip in PIPELINES] is the length of each
    pipeline in kilometers.

    pip_efficiency[pip in PIPELINES] is the proportion of
    energy sent down a line that is delivered. If 2 percent of energy
    sent down a line is lost, this value would be set to 0.98. We 
    assume 0.5% H2 losses per 1000 km for dedicated hydrogen pipelines, 
    as in https://pubs.rsc.org/en/content/articlehtml/2023/se/d3se00281k.
    These H2 losses are quantified and considered fugitive H2 emissions,
    which contribute to the carbon constraint, as H2 is an indirect GHG.

    BLD_YRS_FOR_PIP is the set of pipelines and years in
    which they have been or could be built. This set includes past and
    potential future builds. All future builds must come online in the
    first year of an investment period. This set is composed of two
    elements with members:  (pip, build_year). For existing pipelines
    where the build years are not known, build_year is set to 'Legacy'.

    BLD_YRS_FOR_EXISTING _PIP is a subset of BLD_YRS_FOR_PIP that lists
    builds that happened before the first investment period. For most
    datasets the build year is unknown, so is it always set to 'Legacy'.

    existing_pip_cap_mw[pip in PIPELINES] is a parameter that
    describes how many MW_H2 of capacity has been installed before the
    start of the study. See the 1st paragraph above for details on MW_H2.

    NEW_PIP_BLD_YRS is a subset of BLD_YRS_FOR_PIP that describes
    potential builds.

    BuildPip[(pip, bld_yr) in BLD_YRS_FOR_PIP] is a decision variable
    that describes the transfer capacity in MW_H2 installed on a corridor
    in a given build year. For existing builds, this variable is locked
    to the existing capacity.

    PipCapacityNameplate[(pip, bld_yr) in BLD_YRS_FOR_PIP] is an expression
    that returns the total nameplate transfer capacity of a pipeline
    in a given period in MW_H2. This is the sum of existing and newly-build
    capacity.

    pip_terrain_multiplier[pip in PIPELINES] is
    a cost adjuster applied to each pipeline that reflects the
    additional costs that may be incurred for traversing that specific
    terrain. Crossing mountains or cities will be more expensive than
    crossing plains. This parameter is optional and defaults to 1. This
    parameter should be in the range of 0.5 to 3.

    pip_capital_cost_per_mw_km describes the investment costs of building a
    new pipeline in units of $BASE_YEAR per MW of H2 transfer capacity per
    km. This is optional and defaults to $180/(km * MW_H2). 
    pip_capital_cost_y_int_per_km describes the y-intercept of the investment 
    costs of building a new pipeline in units of $BASE_YEAR per km of pipeline. 
    This is optional and defaults to $408,000/km. These values both come from the 
    linearized costs originally in
    https://www.sciencedirect.com/science/article/pii/S0360319918338552 and cited 
    in https://www.sciencedirect.com/science/article/pii/S0360319919338625. We 
    adapt the units to be per km rather than m and per MW_H2 rather than GW_H2.
    The full equation for investment cost of pipelines is 
    (pip_capital_cost_per_mw_km * BuildPip + pip_capital_cost_y_int_per_km) * pip_length_km,
    where BuildPip is in MW of H2. See the 1st paragraph above for details on MW_H2.
    
    pip_fixed_om_pc describes the fixed Operations and
    Maintenance costs of pipelines as a percent of capital costs. This is optional 
    and defaults to 5%, which comes from the ReEDS model and
    https://iopscience.iop.org/article/10.1088/1748-9326/acacb5.

    pip_lifetime_yrs is the number of years in which a capital
    construction loan for a new pipeline is repaid. This
    optional parameter defaults to 40 years based on 
    https://www.sciencedirect.com/science/article/pii/S0360319919338625.  
    At the end of this time, we assume pipelines will be rebuilt at the same cost.

    pip_cost_hourly[PIPELINES] is the cost of building
    pipelines in units of $BASE_YEAR / MW- transfer-capacity /
    hour. This derived parameter is based on the total annualized
    capital and fixed O&M costs, then divides that by hours per year to
    determine the portion of costs incurred hourly.

    DIRECTIONAL_PIP is a derived set of directional paths that
    hydrogen can flow along pipelines. Each element of this
    set is a two-dimensional entry that describes the origin and
    destination of the flow: (load_zone_from, load_zone_to). Every
    pipeline will generate two entries in this set. Members of
    this set are abbreviated as pip_d where possible, but may be
    abbreviated as pip in situations where brevity is important and it is
    unlikely to be confused with the overall pipeline.

    pip_d_line[pip_d] is the pipeline associated with this
    directional path.

    PIP_BUILDS_IN_PERIOD[p in PERIODS] is an indexed set that
    describes which pipeline builds will be operational in a given
    period. Currently, pipelines are kept online indefinitely,
    with parts being replaced as they wear out.

    PIP_BUILDS_IN_PERIOD[p] will return a subset of (pip, bld_yr)
    in BLD_YRS_FOR_PIP.

    """

    mod.PIPELINES = Set(dimen=1, input_file="h2_pipelines.csv")
    mod.pip_lz1 = Param(mod.PIPELINES, within=mod.LOAD_ZONES, input_file="h2_pipelines.csv")
    mod.pip_lz2 = Param(mod.PIPELINES, within=mod.LOAD_ZONES, input_file="h2_pipelines.csv")
    # we don't do a min_data_check for PIPELINES, because it may be empty for model
    # configurations that are sometimes run with interzonal pipelines and sometimes not
    # (e.g., island interconnect scenarios). However, presence of this column will still be
    # checked by load_data_aug.
    mod.min_data_check('pip_lz1', 'pip_lz2')

    mod.pip_length_km = Param(mod.PIPELINES, within=NonNegativeReals, input_file="h2_pipelines.csv")
    mod.pip_efficiency = Param(
        mod.PIPELINES,
        within=PercentFraction, input_file="h2_pipelines.csv")
    mod.existing_pip_cap_mw = Param(
        mod.PIPELINES,
        within=NonNegativeReals, input_file="h2_pipelines.csv")
    mod.pip_terrain_multiplier = Param(
        mod.PIPELINES,
        within=NonNegativeReals,
        default=1, input_file="h2_pipelines.csv")
    mod.min_data_check(
        'pip_length_km', 'pip_efficiency', 'existing_pip_cap_mw')
    mod.pip_capital_cost_per_mw_km = Param(
        within=NonNegativeReals,
        default=180, input_file="h2_pipeline_params.csv")
    mod.pip_capital_cost_y_int_per_km = Param(
        within=NonNegativeReals,
        default=408000, input_file="h2_pipeline_params.csv")
    mod.pip_lifetime_yrs = Param(
        within=NonNegativeReals,
        default=40, input_file="h2_pipeline_params.csv")
    mod.pip_fixed_om_pc = Param(
        within=PercentFraction,
        default=0.05, input_file="h2_pipeline_params.csv")
    mod.PIP_BLD_YRS = Set(
        dimen=2,
        initialize=mod.PIPELINES * mod.PERIODS,
        filter=lambda m, pip, p: m.pip_capital_cost_per_mw_km != float("inf"))
    mod.BuildPip = Var(mod.PIP_BLD_YRS, within=NonNegativeReals)
    mod.NewPipCapacity = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, period: sum(
            m.BuildPip[pip, bld_yr]
            for bld_yr in m.PERIODS
            if bld_yr <= period and (pip, bld_yr) in m.PIP_BLD_YRS
        )
    )
    mod.PipCapacityNameplate = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, p: m.NewPipCapacity[pip, p] + m.existing_pip_cap_mw[pip])
    
    # An expression to summarize annual costs for the objective
    # function. Units should be total annual future costs in $base_year
    # real dollars. The objective function will convert these to
    # base_year Net Present Value in $base_year real dollars. Multiply capital 
    # costs by capital recover factor to get annual payments. Add annual
    # fixed O&M that are expressed per km of pipeline.
    mod.PipelineCapitalCosts = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, p: 
        (m.NewPipCapacity[pip, p] * m.pip_capital_cost_per_mw_km + pip_capital_cost_y_int_per_km) 
        * m.pip_length_km[pip] * m.pip_terrain_multiplier[pip] * (crf(m.interest_rate, m.pip_lifetime_yrs)
        if (pip, p) in m.PIP_BLD_YRS else 0
    )
    mod.PipelineFixedOMCosts = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, p: 
        m.PipelineCapitalCosts[pip, p] * m.pip_fixed_om_pc 
        if (pip, p) in m.PIP_BLD_YRS else 0
    )
    mod.PipFixedCosts = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            m.PipelineCapitalCosts[pip, p] + m.PipelineFixedOMCosts[pip, p] for pip in m.PIPELINES
        )
    )
    mod.Cost_Components_Per_Period.append('PipFixedCosts')

    def init_DIRECTIONAL_PIP(model):
        pip_dir = set()
        for pip in model.PIPELINES:
            pip_dir.add((model.pip_lz1[pip], model.pip_lz2[pip]))
            pip_dir.add((model.pip_lz2[pip], model.pip_lz1[pip]))
        return pip_dir
    mod.DIRECTIONAL_PIP = Set(
        dimen=2,
        initialize=init_DIRECTIONAL_PIP)
    mod.PIP_CONNECTIONS_TO_ZONE = Set(
        mod.LOAD_ZONES,
        ordered=False,
        initialize=lambda m, lz: set(
            z for z in m.LOAD_ZONES if (z,lz) in m.DIRECTIONAL_PIP))

    def init_pip_d_line(m, zone_from, zone_to):
        for pip in m.PIPELINES:
            if((m.pip_lz1[pip] == zone_from and m.pip_lz2[pip] == zone_to) or
               (m.pip_lz2[pip] == zone_from and m.pip_lz1[pip] == zone_to)):
                return pip
    mod.pip_d_line = Param(
        mod.DIRECTIONAL_PIP,
        within=mod.PIPELINES,
        initialize=init_pip_d_line)


def post_solve(instance, outdir):
    mod = instance
    pip_build_df = pd.DataFrame([
        {
            "PIPELINE": pip,
            "PERIOD": p,
            "pip_lz1": mod.pip_lz1[pip],
            "pip_lz2": mod.pip_lz2[pip],
            "pip_length_km": mod.pip_length_km[pip],
            "pip_efficiency": mod.pip_efficiency[pip],
            "existing_pip_cap_mw": mod.existing_pip_cap_mw[pip],
            "BuildPip": value(mod.BuildPip[pip, p]) if  (pip, p) in mod.BuildPip else ".",
            "PipCapacityNameplate": value(mod.PipCapacityNameplate[pip, p]),
            "TotalAnnualCost": value(mod.PipelineCosts[pip, p])
        } for pip, p in mod.PIPELINES * mod.PERIODS
    ])
    pip_build_df.set_index(["PIPELINE", "PERIOD"], inplace=True)
    write_table(instance, df=pip_build_df, output_file=os.path.join(outdir, "h2_pipelines.csv"))

