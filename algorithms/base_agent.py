from collections import OrderedDict
from glob import glob
import os

import torch
import numpy as np

from ..utils.normalizer import Normalizer
from ..utils.pytorch import to_tensor, center_crop
from ..utils.logger import logger


class BaseAgent(object):
    """ Base class for agents. """

    def __init__(self, config, ob_space):
        self._config = config

        self._ob_norm = Normalizer(
            ob_space, default_clip_range=config.clip_range, clip_obs=config.clip_obs
        )
        self._buffer = None

    def normalize(self, ob):
        """ Normalizes observations. """
        if self._config.ob_norm:
            return self._ob_norm.normalize(ob)
        return ob

    def act(self, ob, is_train=True):
        """ Returns action and the actor's activation given an observation @ob. """
        if hasattr(self, "_rl_agent"):
            return self._rl_agent.act(ob, is_train)

        ob = self.normalize(ob)

        ob = ob.copy()
        for k, v in ob.items():
            if self._config.encoder_type == "cnn" and len(v.shape) == 3:
                ob[k] = center_crop(v, self._config.encoder_image_size)
            else:
                ob[k] = np.expand_dims(ob[k], axis=0)

        with torch.no_grad():
            ob = to_tensor(ob, self._config.device)
            ac, activation, _, _ = self._actor.act(ob, deterministic=not is_train)

        for k in ac.keys():
            ac[k] = ac[k].cpu().numpy().squeeze(0)
            activation[k] = activation[k].cpu().numpy().squeeze(0)

        return ac, activation

    def update_normalizer(self, obs=None):
        """ Updates normalizers. """
        if self._config.ob_norm:
            if obs is None:
                for i in range(len(self._dataset)):
                    self._ob_norm.update(self._dataset[i]["ob"])
                self._ob_norm.recompute_stats()
            else:
                self._ob_norm.update(obs)
                self._ob_norm.recompute_stats()

    def store_episode(self, rollouts):
        """ Stores @rollouts to replay buffer. """
        raise NotImplementedError()

    def is_off_policy(self):
        raise NotImplementedError()

    def set_buffer(self, buffer):
        self._buffer = buffer

    def replay_buffer(self):
        return self._buffer.state_dict()

    def save_replay_buffer(self, log_dir, ckpt_num):
        replay_path = os.path.join(log_dir, "replay_%08d.pkl" % ckpt_num)
        torch.save(self.replay_buffer(), replay_path)

    def load_replay_buffer(self, log_dir, ckpt_num):
        replay_paths = glob(os.path.join(log_dir, "replay_*.pkl"))
        replay_paths.sort()
        load_replay = False
        for replay_path in replay_paths:
            start_idx = int(replay_path.split(".")[0].split("_")[1])
            end_idx = int(replay_path.split(".")[0].split("_")[2])
            if not (end_idx <= ckpt_num - self._replay_buffer.size or ckpt_num < start_idx):
                continue
            logger.warn("Load replay_buffer %s", replay_path)
            buffer = torch.load(replay_path)
            self._replay_buffer.add(buffer)
            load_replay = True

        if not load_replay:
            logger.warn("Replay buffer not exists at %s", log_dir)

    # def load_replay_buffer(self, state_dict):
    #     self._buffer.load_state_dict(state_dict)

    def set_reward_function(self, predict_reward):
        self._predict_reward = predict_reward

    def sync_networks(self):
        raise NotImplementedError()

    def train(self):
        raise NotImplementedError()

    def _soft_update_target_network(self, target, source, tau):
        for target_param, source_param in zip(target.parameters(), source.parameters()):
            target_param.data.copy_(
                (1 - tau) * source_param.data + tau * target_param.data
            )

    def _copy_target_network(self, target, source):
        self._soft_update_target_network(target, source, 0)
