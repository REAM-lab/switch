import pandas as pd

from switch_model.wecc.get_inputs.register_post_process import post_process_step
from switch_model.tools.drop import main as drop

@post_process_step(msg="Removing emitting plants if there's a zero-emissions constraint")
def post_process(_):
    # Check that there's a no emissions constraint on all the years.
    carbon_policies = pd.read_csv("carbon_policies.csv", index_col=False, na_values=".")
    if not (carbon_policies["carbon_cap_tco2_per_yr"] == 0).all():
        return

    # If so, find the list of emitting fuels.
    fuels = pd.read_csv("fuels.csv", index_col=False, na_values=".")
    fuels = fuels[fuels["co2_intensity"] != 0]

    # Now remove the projects that use that fuel.
    gen_proj = pd.read_csv("generation_projects_info.csv", index_col=False, na_values=".", dtype={"GENERATION_PROJECT": str})
    l1 = len(gen_proj)
    gen_proj = gen_proj[~gen_proj["gen_energy_source"].isin(fuels["fuel"])]
    l2 = len(gen_proj)
    gen_proj.to_csv("generation_projects_info.csv", index=False)

    # Now remove references to that project
    drop(["--silent", "--no-confirm", "--run", "--inputs-dir", "."])