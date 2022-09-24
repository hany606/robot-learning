"""
Converting dm_control to gym is from https://github.com/denisyarats/dmc2gym
"""

import os
from collections import deque, OrderedDict

import gym
import numpy as np

from . import Logger
from .subproc_vec_env import SubprocVecEnv


def make_env(id, cfg=None, seed=0, name=None):
    """Creates a new environment instance with `id` and `cfg`."""
    # create a maze environment
    if id == "maze":
        from envs.maze import ACRandMaze0S40Env  # TODO: Use gym interface.

        env = ACRandMaze0S40Env(cfg)

        # no need to wrap for spirl
        if name == "spirl":
            return env
        env = GymWrapper(
            env=env,
            from_pixels=cfg.pixel_ob,
            from_state=cfg.state_ob,
            height=cfg.screen_size[0],
            width=cfg.screen_size[1],
            channels_first=False,
            frame_skip=cfg.action_repeat,
            return_state=False,  # TODO: Do we need this?
            seed=seed,
        )
        return wrap_env(env, cfg)

    # create a kitchen environment
    elif id == "kitchen":
        from envs.kitchen import (
            NoGoalKitchenEnv,
            KitchenEnv,
        )  # TODO: Use gym interface.

        # skill prior checkpoint has state dim as 60
        env_class = (
            NoGoalKitchenEnv
            if name not in ["spirl", "spirl_dreamer", "spirl_tdmpc"]
            else KitchenEnv
        )
        env = env_class(cfg)
        # no need to wrap for spirl
        if name == "spirl":
            return env
        env = GymWrapper(
            env=env,
            from_pixels=cfg.pixel_ob,
            from_state=cfg.state_ob,
            height=cfg.screen_size[0],
            width=cfg.screen_size[1],
            channels_first=False,
            frame_skip=cfg.action_repeat,
            return_state=False,
            seed=seed,
        )
        return wrap_env(env, cfg)

    # create a calvin environment
    elif id == "calvin":
        from envs.calvin import CalvinEnv

        env = CalvinEnv(**cfg)
        # no need to wrap for spirl
        if name == "spirl":
            return env
        env = GymWrapper(
            env=env,
            from_pixels=cfg.pixel_ob,
            from_state=cfg.state_ob,
            height=cfg.screen_size[0],
            width=cfg.screen_size[1],
            channels_first=False,
            frame_skip=cfg.action_repeat,
            return_state=False,
            seed=seed,
        )
        return wrap_env(env, cfg)

    # get default config if not provided
    if cfg is None:
        cfg = {
            "id": id,
            "env_cfg": {
                "action_repeat": 1,
                "screen_size": [512, 512],
                "pixel_ob": False,
                "state_ob": True,
                "absorbing_state": False,
            },
        }
    return get_gym_env(id, cfg, seed)


def get_gym_env(env_id, cfg, seed):
    """Creates gym environment and wraps with `DictWrapper` and `ActionNormWrapper`."""
    env_cfg = cfg["env_cfg"]
    env_kwargs = cfg.copy()
    del env_kwargs["env_cfg"]
    del env_kwargs["id"]

    if env_id.startswith("dm."):
        os.environ["MUJOCO_GL"] = "egl"

        # Environment name of dm_control: dm.DOMAIN_NAME.TASK_NAME
        _, domain_name, task_name = env_id.split(".")
        # Use closer camera for quadruped
        camera_id = 2 if domain_name == "quadruped" else 0
        env = DMCGymEnv(
            domain_name=domain_name,
            task_name=task_name,
            seed=seed,
            height=env_cfg.screen_size[0],
            width=env_cfg.screen_size[1],
            camera_id=camera_id,
        )
    else:
        try:
            env = gym.make(env_id, **env_kwargs)
        except Exception as e:
            Logger.warning("Failed to launch an environment with config.")
            Logger.warning(e)
            Logger.warning("Launch an environment without config.")
            env = gym.make(env_id)

    env = GymWrapper(
        env=env,
        from_pixels=env_cfg.pixel_ob,
        from_state=env_cfg.state_ob,
        height=env_cfg.screen_size[0],
        width=env_cfg.screen_size[1],
        channels_first=False,
        frame_skip=env_cfg.action_repeat,
        return_state=False,  # TODO: Do we need this?
        seed=seed,
    )

    return wrap_env(env, env_cfg)


class DMCGymEnv(gym.Env):
    def __init__(
        self,
        domain_name,
        task_name,
        seed=0,
        height=84,
        width=84,
        camera_id=0,
    ):
        from dm_control import suite

        self._env = suite.load(domain_name, task_name, task_kwargs=dict(random=seed))
        self.render_mode = "rgb_array"
        self.height = height
        self.width = width
        self.camera_id = camera_id

    @property
    def action_space(self):
        spec = self._env.action_spec()
        return gym.spaces.Box(spec.minimum, spec.maximum, dtype=np.float32)

    @property
    def observation_space(self):
        spec = self._env.observation_spec()
        spaces = OrderedDict()
        from dm_env import specs

        def type_map(dtype):
            if dtype == np.uint8:
                return np.uint8
            elif dtype == np.float32 or dtype == np.float64:
                return np.float32
            else:
                raise NotImplementedError(dtype)

        for k, v in spec.items():
            if type(v) == specs.Array:
                spaces[k] = gym.spaces.Box(-np.inf, np.inf, v.shape, type_map(v.dtype))
            elif type(v) == specs.BoundedArray:
                spaces[k] = gym.spaces.Box(
                    -v.minimum, v.maximum, v.shape, type_map(v.dtype)
                )
        return gym.spaces.Dict(spaces)

    def reset(self, seed=None):
        timestep = self._env.reset()
        return timestep.observation, {}

    def step(self, action):
        timestep = self._env.step(action)
        ob = timestep.observation
        reward = timestep.reward or 0.0
        truncated = timestep.discount == 0
        terminated = timestep.last() and not truncated
        return ob, reward, terminated, truncated, {}

    def render(self):
        return self._env.physics.render(
            height=self.height, width=self.width, camera_id=self.camera_id
        )


def wrap_env(env, cfg):
    env = DictWrapper(env, return_state=False)  # TODO: Do we need this?
    env = ActionNormWrapper(env)

    if cfg.pixel_ob and cfg.frame_stack > 1:
        env = FrameStackWrapper(
            env,
            frame_stack=3,
            return_state=cfg.pixel_ob and cfg.state_ob,
        )
    if cfg.absorbing_state:
        env = AbsorbingWrapper(env)

    return env


def make_vec_env(env_id, num_env, cfg=None, seed=0):
    """
    Creates a wrapped SubprocVecEnv using OpenAI gym interface.
    Unity app will use the port number from @cfg.port to (@cfg.port + @num_env - 1).

    Code modified based on
    https://github.com/openai/baselines/blob/master/baselines/common/cmd_util.py

    Args:
        env_id: environment id registered in in `env/__init__.py`.
        num_env: number of environments to launch.
        cfg: general configuration for the environment.
    """
    env_kwargs = {}

    if cfg is not None:
        for key, value in cfg.__dict__.items():
            env_kwargs[key] = value

    def make_thunk(rank):
        new_env_kwargs = env_kwargs.copy()
        if "port" in new_env_kwargs:
            new_env_kwargs["port"] = env_kwargs["port"] + rank
        return lambda: get_gym_env(env_id, new_env_kwargs, seed + rank)

    return SubprocVecEnv([make_thunk(i) for i in range(num_env)])


def cat_spaces(spaces):
    if isinstance(spaces[0], gym.spaces.Box):
        out_space = gym.spaces.Box(
            low=np.concatenate([s.low for s in spaces]),
            high=np.concatenate([s.high for s in spaces]),
        )
    elif isinstance(spaces[0], gym.spaces.Discrete):
        out_space = gym.spaces.Discrete(sum([s.n for s in spaces]))
    return out_space


def stacked_space(space, k):
    if isinstance(space, gym.spaces.Box):
        space_stack = gym.spaces.Box(
            low=np.concatenate([space.low] * k, axis=-1),
            high=np.concatenate([space.high] * k, axis=-1),
            dtype=space.dtype,
        )
    elif isinstance(space, gym.spaces.Discrete):
        space_stack = gym.spaces.Discrete(space.n * k)
    return space_stack


def value_to_space(value):
    if isinstance(value, dict):
        space = gym.spaces.Dict([(k, value_to_space(v)) for k, v in value.items()])
    elif isinstance(value, np.ndarray):
        space = gym.spaces.Box(low=-np.inf, high=np.inf, shape=value.shape)
    else:
        raise NotImplementedError

    return space


def space_to_shape(space):
    if isinstance(space, gym.spaces.Dict):
        return {k: space_to_shape(v) for k, v in space.spaces.items()}
    elif isinstance(space, gym.spaces.Box):
        return space.shape
    elif isinstance(space, gym.spaces.Discrete):
        return [space.n]


def zero_value(space, dtype=np.float64):
    if isinstance(space, gym.spaces.Dict):
        return OrderedDict(
            [(k, zero_value(space, dtype)) for k, space in space.spaces.items()]
        )
    elif isinstance(space, gym.spaces.Box):
        return np.zeros(space.shape).astype(dtype)
    elif isinstance(space, gym.spaces.Discrete):
        return np.zeros(1).astype(dtype)


def get_non_absorbing_state(ob):
    ob = ob.copy()
    ob["absorbing_state"] = np.array([0])
    return ob


def get_absorbing_state(space):
    ob = zero_value(space)
    ob["absorbing_state"] = np.array([1])
    return ob


class GymWrapper(gym.Wrapper):
    def __init__(
        self,
        env,
        from_pixels=False,
        from_state=True,
        height=100,
        width=100,
        camera_id=None,
        channels_first=True,
        frame_skip=1,
        return_state=False,
        seed=None,
    ):
        super().__init__(env)
        self._from_pixels = from_pixels
        self._from_state = from_state
        self._height = height
        self._width = width
        self._camera_id = camera_id
        self._channels_first = channels_first
        self._frame_skip = frame_skip
        self._return_state = return_state
        self._seed = seed

        if from_pixels:
            shape = [3, height, width] if channels_first else [height, width, 3]
            self.observation_space = gym.spaces.Box(
                low=0, high=255, shape=shape, dtype=np.uint8
            )
        else:
            self.observation_space = env.observation_space

        if from_pixels and from_state:
            self.observation_space = gym.spaces.Dict(
                OrderedDict(
                    [
                        ("image", self.observation_space),
                        ("state", env.observation_space),
                    ]
                )
            )

    def reset(self, seed=None):
        ob, info = self.env.reset(seed=self._seed)
        self._seed = None  # Initialize seed only once.

        if self._return_state:
            return self._get_obs(ob, reset=True), ob, info

        return self._get_obs(ob, reset=True), info

    def step(self, ac):
        reward = 0
        for _ in range(self._frame_skip):
            ob, _reward, terminated, truncated, info = self.env.step(ac)
            done = terminated or truncated
            reward += _reward
            if done:
                break
        if self._return_state:
            return (self._get_obs(ob), ob), reward, terminated, truncated, info

        return self._get_obs(ob), reward, terminated, truncated, info

    def _get_obs(self, ob, reset=False):
        state = ob
        if self._from_pixels:
            ob = self.render(
                mode="rgb_array",
                height=self._height,
                width=self._width,
                camera_id=self._camera_id,
            )
            if reset:
                ob = self.render(
                    mode="rgb_array",
                    height=self._height,
                    width=self._width,
                    camera_id=self._camera_id,
                )
            if self._channels_first:
                ob = ob.transpose(2, 0, 1).copy()
        else:
            return state

        if self._from_pixels and self._from_state:
            return OrderedDict([("image", ob), ("state", state)])
        return ob

    def render(self, mode=None, height=None, width=None, camera_id=None):
        if mode is not None:
            self.unwrapped.render_mode = mode
        if height is not None:
            self.unwrapped.height = height
        if width is not None:
            self.unwrapped.width = width
        if camera_id is not None:
            self.unwrapped.camera_id = camera_id
        return self.env.render()


class DictWrapper(gym.Wrapper):
    def __init__(self, env, return_state=False):
        super().__init__(env)

        self._return_state = return_state

        self._is_ob_dict = isinstance(env.observation_space, gym.spaces.Dict)
        if not self._is_ob_dict:
            self.key = "image" if len(env.observation_space.shape) == 3 else "ob"
            self.observation_space = gym.spaces.Dict({self.key: env.observation_space})
        else:
            self.observation_space = env.observation_space

        self._is_ac_dict = isinstance(env.action_space, gym.spaces.Dict)
        if not self._is_ac_dict:
            self.action_space = gym.spaces.Dict({"ac": env.action_space})
        else:
            self.action_space = env.action_space

    def reset(self, seed=None):
        ob, info = self.env.reset(seed=seed)
        return self._get_obs(ob), info

    def step(self, ac):
        if not self._is_ac_dict:
            ac = ac["ac"]
        ob, reward, terminated, truncated, info = self.env.step(ac)
        return self._get_obs(ob), reward, terminated, truncated, info

    def _get_obs(self, ob):
        if not self._is_ob_dict:
            if self._return_state:
                ob = {self.key: ob[0], "state": ob[1]}
            else:
                ob = {self.key: ob}
        return ob


class FrameStackWrapper(gym.Wrapper):
    def __init__(self, env, frame_stack=3, return_state=False):
        super().__init__(env)

        # Both observation and action spaces must be gym.spaces.Dict.
        assert isinstance(env.observation_space, gym.spaces.Dict), env.observation_space
        assert isinstance(env.action_space, gym.spaces.Dict), env.action_space

        self._frame_stack = frame_stack
        self._frames = deque([], maxlen=frame_stack)
        self._return_state = return_state
        self._state = None

        ob_space = []
        for k, space in env.observation_space.spaces.items():
            space_stack = stacked_space(space, frame_stack)
            ob_space.append((k, space_stack))
        self.observation_space = gym.spaces.Dict(ob_space)

    def reset(self, seed=None):
        ob, info = self.env.reset(seed=seed)
        if self._return_state:
            self._state = ob.pop("state", None)
        for _ in range(self._frame_stack):
            self._frames.append(ob)
        return self._get_obs(), info

    def step(self, ac):
        ob, reward, terminated, truncated, info = self.env.step(ac)
        if self._return_state:
            self._state = ob.pop("state", None)
        self._frames.append(ob)
        return self._get_obs(), reward, terminated, truncated, info

    def _get_obs(self):
        frames = list(self._frames)
        obs = []
        for k in self.env.observation_space.spaces.keys():
            obs.append((k, np.concatenate([f[k] for f in frames], axis=-1)))
        if self._return_state:
            obs.append(("state", self._state))

        return OrderedDict(obs)


class ActionNormWrapper(gym.Wrapper):
    def __init__(self, env):
        super().__init__(env)

        assert isinstance(env.action_space, gym.spaces.Dict), env.action_space

        ac_space = []
        self._low = {}
        self._high = {}
        for k, space in env.action_space.spaces.items():
            if isinstance(space, gym.spaces.Box):
                self._low[k] = low = space.low
                self._high[k] = high = space.high
                space = gym.spaces.Box(
                    -np.ones_like(low), np.ones_like(high), dtype=np.float32
                )
            ac_space.append((k, space))
        self.action_space = gym.spaces.Dict(ac_space)

    def step(self, action):
        action = action.copy()
        for k in self._low:
            action[k] = (action[k] + 1) / 2 * (
                self._high[k] - self._low[k]
            ) + self._low[k]
            action[k] = np.clip(action[k], self._low[k], self._high[k])
        return self.env.step(action)


class AbsorbingWrapper(gym.Wrapper):
    def __init__(self, env):
        super().__init__(env)
        ob_space = gym.spaces.Dict(spaces=dict(env.observation_space.spaces))
        ob_space.spaces["absorbing_state"] = gym.spaces.Box(
            low=-1, high=1, shape=(1,), dtype=np.uint8
        )
        self.observation_space = ob_space

    def reset(self, seed=None):
        ob, info = self.env.reset(seed=seed)
        return self._get_obs(ob), info

    def step(self, ac):
        ob, reward, terminated, truncated, info = self.env.step(ac)
        return self._get_obs(ob), reward, terminated, truncated, info

    def _get_obs(self, ob):
        return get_non_absorbing_state(ob)

    def get_absorbing_state(self):
        return get_absorbing_state(self.observation_space)
