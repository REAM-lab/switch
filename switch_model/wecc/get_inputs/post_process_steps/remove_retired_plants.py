import pandas as pd

from switch_model.generators.core.build import is_plant_retired
from switch_model.wecc.get_inputs.register_post_process import post_process_step
from switch_model.tools.drop import main as drop


@post_process_step(msg="Removing plants that are retired before the first period")
def post_process(_):
    # Get the first period
    # Note we get the first period based on the order in periods.csv which is not ideal
    periods = pd.read_csv("periods.csv", index_col=False)
    first_period = periods.iloc[0]
    period_start = first_period.period_start
    # Note the period_length could be (end - start + 1) or just (end - start).
    # This depends on how the period was specified.
    # To be conservative we use the version with the +1 so that the cutoff is later
    # And like that we don't accidentally remove a non-retired plant.
    period_length = first_period.period_end - first_period.period_start + 1
    print(first_period)

    # Get the last build year for each plant.
    build_yrs = pd.read_csv(
        "gen_build_costs.csv",
        index_col=False,
        na_values=".",
        dtype={"GENERATION_PROJECT": str},
    )
    build_yrs = build_yrs.groupby("GENERATION_PROJECT", as_index=False).build_year.max()

    # Find if each project is retired
    def is_retired(row):
        return is_plant_retired(row.build_year, period_start, period_length, row.gen_max_age)
    proj = pd.read_csv(
        "generation_projects_info.csv",
        index_col=False,
        na_values=".",
        dtype={"GENERATION_PROJECT": str},
    )
    columns = proj.columns
    proj = proj.merge(build_yrs, on="GENERATION_PROJECT")
    proj["is_retired"] = proj.apply(is_retired, axis=1)

    # Filter out retired projects
    l1 = len(proj)
    proj = proj[~proj["is_retired"]]
    l2 = len(proj)
    print(f"Dropped {l1 - l2} projects.")

    # Write csv
    proj[columns].to_csv("generation_projects_info.csv", index=False)

    # Now remove references to those projects
    drop(["--silent", "--no-confirm", "--run", "--inputs-dir", "."])




