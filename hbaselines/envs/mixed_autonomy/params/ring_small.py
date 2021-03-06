"""Ring road example."""
import numpy as np

from flow.envs import WaveAttenuationPOEnv
from flow.envs.multiagent import MultiAgentWaveAttenuationPOEnv
from flow.networks import RingNetwork
from flow.core.params import SumoParams, EnvParams, InitialConfig, NetParams
from flow.core.params import VehicleParams, SumoCarFollowingParams
from flow.controllers import RLController, IDMController, ContinuousRouter

# maximum acceleration for autonomous vehicles, in m/s^2
MAX_ACCEL = 1
# maximum deceleration for autonomous vehicles, in m/s^2
MAX_DECEL = 1


def full_observation_fn(env):
    """Compute the full state observation.

    This observation consists of the speeds and bumper-to-bumper headways of
    all automated vehicles in the network.
    """
    obs = []
    for rl_id in env.k.vehicle.get_rl_ids():
        lead_id = env.k.vehicle.get_leader(rl_id) or rl_id

        # normalizers
        max_speed = 15.
        max_length = env.env_params.additional_params['ring_length'][1]

        obs.extend([
            env.k.vehicle.get_speed(rl_id) / max_speed,
            (env.k.vehicle.get_speed(lead_id) -
             env.k.vehicle.get_speed(rl_id))
            / max_speed,
            env.k.vehicle.get_headway(rl_id) / max_length
        ])

    return np.asarray(obs)


def get_flow_params(num_automated=1,
                    horizon=7500,
                    ring_length=None,
                    simulator="traci",
                    evaluate=False,
                    multiagent=False):
    """Return the flow-specific parameters of the smaller ring road network.

    This scenario consists of 21 human-driven vehicles and one automated
    vehicle placed on a sing-lane circular track whose length is varied for
    values ranging between 220m and 270m (uniformly sampled). In the absence of
    the automated vehicle, the 22 human-driven vehicles exhibit stop-and-go
    instabilities brought about by the string-unstable characteristic of human
    car-following dynamics.

    This benchmark is adapted from the following article:

    Wu, Cathy, et al. "Flow: A Modular Learning Framework for Autonomy in
    Traffic." arXiv preprint arXiv:1710.05465 (2017).

    Parameters
    ----------
    num_automated : int
        number of automated (RL) vehicles
    horizon : int
        time horizon of a single rollout
    ring_length : [int, int]
        the minimum and maximum ring indices during simulation. Set the two to
        the same value to get a single deterministic radius.
    simulator : str
        the simulator used, one of {'traci', 'aimsun'}
    evaluate : bool
        whether to compute the evaluation reward
    multiagent : bool
        whether the automated vehicles are via a single-agent policy or a
        shared multi-agent policy with the actions of individual vehicles
        assigned by a separate policy call

    Returns
    -------
    dict
        flow-related parameters, consisting of the following keys:

        * exp_tag: name of the experiment
        * env_name: environment class of the flow environment the experiment
          is running on. (note: must be in an importable module.)
        * network: network class the experiment uses.
        * simulator: simulator that is used by the experiment (e.g. aimsun)
        * sim: simulation-related parameters (see flow.core.params.SimParams)
        * env: environment related parameters (see flow.core.params.EnvParams)
        * net: network-related parameters (see flow.core.params.NetParams and
          the network's documentation or ADDITIONAL_NET_PARAMS component)
        * veh: vehicles to be placed in the network at the start of a rollout
          (see flow.core.params.VehicleParams)
        * initial (optional): parameters affecting the positioning of vehicles
          upon initialization/reset (see flow.core.params.InitialConfig)
        * tls (optional): traffic lights to be introduced to specific nodes
          (see flow.core.params.TrafficLightParams)
    """
    num_human = 22 - num_automated
    humans_remaining = num_human

    vehicles = VehicleParams()
    for i in range(num_automated):
        # Add one automated vehicle.
        vehicles.add(
            veh_id="rl_{}".format(i),
            acceleration_controller=(RLController, {}),
            routing_controller=(ContinuousRouter, {}),
            num_vehicles=1)

        # Add a fraction of the remaining human vehicles.
        vehicles_to_add = round(humans_remaining / (num_automated - i))
        humans_remaining -= vehicles_to_add
        vehicles.add(
            veh_id="human_{}".format(i),
            acceleration_controller=(IDMController, {
                "noise": 0.2
            }),
            car_following_params=SumoCarFollowingParams(
                min_gap=0
            ),
            routing_controller=(ContinuousRouter, {}),
            num_vehicles=vehicles_to_add)

    # In case the above code has a bug, this will catch it.
    assert vehicles.num_vehicles == 22, \
        "{} not equal to 22".format(vehicles.num_vehicles)

    return dict(
        # name of the experiment
        exp_tag="stabilizing_the_ring",

        # name of the flow environment the experiment is running on
        env_name=(MultiAgentWaveAttenuationPOEnv if multiagent else
                  WaveAttenuationPOEnv),

        # name of the network class the experiment is running on
        network=RingNetwork,

        # simulator that is used by the experiment
        simulator=simulator,

        # sumo-related parameters (see flow.core.params.SumoParams)
        sim=SumoParams(
            sim_step=0.1,
            render=False,
        ),

        # environment related parameters (see flow.core.params.EnvParams)
        env=EnvParams(
            horizon=horizon,
            warmup_steps=750,
            clip_actions=False,
            evaluate=evaluate,
            additional_params={
                "max_accel": MAX_ACCEL,
                "max_decel": MAX_DECEL,
                "ring_length": ring_length or [220, 270],
                "full_observation_fn": full_observation_fn
            },
        ),

        # network-related parameters (see flow.core.params.NetParams and the
        # network's documentation or ADDITIONAL_NET_PARAMS component)
        net=NetParams(
            additional_params={
                "length": 260,
                "lanes": 1,
                "speed_limit": 30,
                "resolution": 40,
            },
        ),

        # vehicles to be placed in the network at the start of a rollout (see
        # flow.core.params.VehicleParams)
        veh=vehicles,

        # parameters specifying the positioning of vehicles upon init/reset
        # (see flow.core.params.InitialConfig)
        initial=InitialConfig(),
    )
