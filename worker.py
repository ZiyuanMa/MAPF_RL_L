import ray
import time
import random
import os
import torch
import torch.nn as nn
from torch.optim import Adam, lr_scheduler
import numpy as np
from copy import deepcopy
from typing import List
import threading

import config
from model_dqn import Network
from environment import Environment
from buffer import SumTree, LocalBuffer
from search import find_path

@ray.remote(num_cpus=2)
class GlobalBuffer:
    def __init__(self, capacity, beta=config.prioritized_replay_beta):
        self.capacity = capacity
        self.size = 0
        self.ptr = 0
        self.buffer = [ None for _ in range(capacity) ]
        self.priority_tree = SumTree(capacity)
        self.beta = beta
        self.counter = 0

    def __len__(self):
        return self.size

    def add(self, buffer:LocalBuffer):
        
        # update buffer size
        if self.buffer[self.ptr] is not None:
            self.size -= len(self.buffer[self.ptr])
        self.size += len(buffer)
        self.counter += len(buffer)

        buffer.priority_tree.tree.flags.writeable = True

        self.buffer[self.ptr] = buffer

        # print('tree add 0')
        self.priority_tree.update(self.ptr, buffer.priority)
        # print('tree add 1')
        # print('ptr: {}, current size: {}, add priority: {}, current: {}'.format(self.ptr, self.size, buffer.priority, self.priority_tree.sum()))

        self.ptr = (self.ptr+1) % self.capacity
    
    def batch_add(self, buffers:List[LocalBuffer]):
        for buffer in buffers:
            self.add(buffer)

    def sample_batch(self, batch_size):
        if len(self) < config.learning_starts:
            return None

        total_p = self.priority_tree.sum()

        b_obs, b_pos, b_action, b_reward, b_next_obs, b_next_pos, b_done, b_steps, b_bt_steps, b_next_bt_steps = [], [], [], [], [], [], [], [], [], []
        idxes, priorities = [], []

        every_range_len = total_p / batch_size
        for i in range(batch_size):
            global_prefixsum = random.random() * every_range_len + i * every_range_len
            global_idx, local_prefixsum = self.priority_tree.find_prefixsum_idx(global_prefixsum)
            ret = self.buffer[global_idx].sample(local_prefixsum)
            obs, pos, action, reward, next_obs, next_pos, done, steps, bt_steps, next_bt_steps, local_idx, priority = ret   
            
            b_obs.append(obs)
            b_pos.append(pos)
            b_action += action
            b_reward += reward
            b_next_obs.append(next_obs)
            b_next_pos.append(next_pos)

            b_done += done
            b_steps += steps
            b_bt_steps += bt_steps
            b_next_bt_steps += next_bt_steps

            idxes.append(global_idx*config.max_steps+local_idx)
            priorities.append(priority)

        priorities = np.array(priorities, dtype=np.float32)
        min_p = np.min(priorities)
        weights = np.power(priorities/min_p, -self.beta)

        data = (
            torch.from_numpy(np.concatenate(b_obs).astype(np.float32)),
            torch.from_numpy(np.concatenate(b_pos).astype(np.float32)),
            torch.LongTensor(b_action).unsqueeze(1),
            torch.FloatTensor(b_reward).unsqueeze(1),
            torch.from_numpy(np.concatenate(b_next_obs).astype(np.float32)),
            torch.from_numpy(np.concatenate(b_next_pos).astype(np.float32)),

            torch.FloatTensor(b_done).unsqueeze(1),
            torch.FloatTensor(b_steps).unsqueeze(1),
            b_bt_steps,
            b_next_bt_steps,

            idxes,
            torch.from_numpy(weights).unsqueeze(1)
        )

        self.temp = self.ptr

        return data

    def update_priorities(self, idxes:List[int], priorities:List[float]):
        """Update priorities of sampled transitions"""

        idxes = np.asarray(idxes)
        priorities = np.asarray(priorities)

        # discard the idx that already been discarded during training
        if self.ptr > self.temp:
            # range from [self.temp, self.ptr)
            mask = (idxes < self.temp*config.max_steps) | (idxes >= self.ptr*config.max_steps)
            idxes = idxes[mask]
            priorities = priorities[mask]
        elif self.ptr < self.temp:
            # range from [0, self.ptr) & [self.temp, self,capacity)
            mask = (idxes < self.temp*config.max_steps) & (idxes >= self.ptr*config.max_steps)
            idxes = idxes[mask]
            priorities = priorities[mask]

        global_idxes = idxes // config.max_steps
        local_idxes = idxes % config.max_steps

        for global_idx, local_idx, priority in zip(global_idxes, local_idxes, priorities):
            assert priority > 0

            self.buffer[global_idx].update_priority(local_idx, priority)

        global_idxes = np.unique(global_idxes)
        new_p = []
        for global_idx in global_idxes:
            new_p.append(self.buffer[global_idx].priority)

        new_p = np.asarray(new_p)
        self.priority_tree.batch_update(global_idxes, new_p)

    def stats(self, interval:int):
        print('buffer update: {}'.format(self.counter/interval))
        print('buffer size: {}'.format(self.size))
        self.counter = 0

    def ready(self):
        if len(self) >= config.learning_starts:
            return True
        else:
            return False

@ray.remote(num_cpus=2, num_gpus=1)
class Learner:
    def __init__(self, buffer):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.model = Network()
        self.model.to(self.device)
        self.tar_model = deepcopy(self.model)
        self.optimizer = Adam(self.model.parameters(), lr=2.5e-4)
        self.buffer = buffer
        self.counter = 0
        self.done = False
        self.loss = 0

        state_dict = self.model.state_dict()
        for k, v in state_dict.items():
            state_dict[k] = v.cpu()
        weight_id = ray.put(state_dict)
        self.weight_id = weight_id

    
    def get_weights(self):

        return self.weight_id

    def run(self):
        self.learning_thread = threading.Thread(target=self.train, daemon=True)
        self.learning_thread.start()

    def train(self):

        min_value = -5
        max_value = 5
        atom_num = 51
        delta_z = 10 / 50
        z_i = torch.linspace(-5, 5, 51).to(self.device)

        for i in range(1, 500001):

            data = ray.get(self.buffer.sample_batch.remote(config.batch_size))
 
            b_obs, b_pos, b_action, b_reward, b_next_obs, b_next_pos, b_done, b_steps, b_bt_steps, b_next_bt_steps, idxes, weights = data
            b_obs, b_pos, b_action, b_reward = b_obs.to(self.device), b_pos.to(self.device), b_action.to(self.device), b_reward.to(self.device)
            b_next_obs, b_next_pos, b_done, b_steps, weights = b_next_obs.to(self.device), b_next_pos.to(self.device), b_done.to(self.device), b_steps.to(self.device), weights.to(self.device)

            with torch.no_grad():
                b_next_dist = self.tar_model.bootstrap(b_next_obs, b_next_pos, b_next_bt_steps).exp()
                b_next_action = (b_next_dist * z_i).sum(-1).argmax(1)
                b_tzj = ((0.99**b_steps) * (1 - b_done) * z_i[None, :] + b_reward).clamp(min_value, max_value)
                b_i = (b_tzj - min_value) / delta_z
                b_l = b_i.floor()
                b_u = b_i.ceil()
                b_m = torch.zeros(config.batch_size*config.num_agents, atom_num).to(self.device)
                temp = b_next_dist[torch.arange(config.batch_size*config.num_agents), b_next_action, :]
                b_m.scatter_add_(1, b_l.long(), temp * (b_u - b_i))
                b_m.scatter_add_(1, b_u.long(), temp * (b_i - b_l))
            
            # del b_next_obs, b_next_pos

            b_q = self.model.bootstrap(b_obs, b_pos, b_bt_steps)[torch.arange(config.batch_size*config.num_agents), b_action.squeeze(1), :]

            kl_error = (-b_q*b_m).sum(dim=1).reshape(config.batch_size, config.num_agents).mean(dim=1)
            # use kl error as priorities as proposed by Rainbow
            priorities = kl_error.detach().cpu().clamp(1e-6).numpy()
            loss = kl_error.mean()

            self.optimizer.zero_grad()

            loss.backward()
            self.loss = loss.item()
            # scaler.scale(loss).backward()

            nn.utils.clip_grad_norm_(self.model.parameters(), 40)

            self.optimizer.step()
            # scaler.step(optimizer)
            # scaler.update()

            # scheduler.step()

            # store new weight in shared memory
            state_dict = self.model.state_dict()
            for k, v in state_dict.items():
                state_dict[k] = v.cpu()
            weights_id = ray.put(state_dict)
            self.weights_id = weights_id

            self.buffer.update_priorities.remote(idxes, priorities)

            self.counter += 1

            # update target net
            if i % 1250 == 0:
                self.tar_model.load_state_dict(self.model.state_dict())
                
            # save model
            if i % 5000 == 0:
                torch.save(self.model.state_dict(), os.path.join(config.save_path, '{}.pth'.format(i)))

        self.done = True
    
    def stats(self, interval:int):
        print('updates: {}'.format(self.counter))
        print('loss: {}'.format(self.loss))
        # self.counter = 0
        return self.done

@ray.remote(num_cpus=1)
class Actor:
    def __init__(self, worker_id, epsilon, learner:Learner, buffer:GlobalBuffer):
        self.id = worker_id
        self.model = Network()
        self.model.eval()
        self.env = Environment()
        self.epsilon = epsilon
        self.learner = learner
        self.buffer = buffer
        self.distributional = config.distributional
        self.imitation_ratio = config.imitation_ratio
        self.max_steps = config.max_steps

    def run(self):
        """ Generate training batch sample """
        done = False

        if self.distributional:
            vrange = torch.linspace(-5, 5, 51)

        # if use imitation learning
        imitation = True if random.random() < self.imitation_ratio else False
        if imitation:
            imitation_actions = find_path(self.env)
            while imitation_actions is None:
                self.env.reset()
                imitation_actions = find_path(self.env)
            obs_pos = self.env.observe()
            buffer = LocalBuffer(obs_pos, True)
        else:
            obs_pos = self.env.reset()
            buffer = LocalBuffer(obs_pos, False)

        buffers = []

        while True:

            if imitation:

                actions = imitation_actions.pop(0)
                with torch.no_grad():
                    q_val = self.model.step(torch.FloatTensor(obs_pos[0]), torch.FloatTensor(obs_pos[1]))
                    if self.distributional:
                            q_val = (q_val.exp() * vrange).sum(2)

            else:
                # sample action
                with torch.no_grad():

                    q_val = self.model.step(torch.FloatTensor(obs_pos[0]), torch.FloatTensor(obs_pos[1]))

                    if self.distributional:
                        q_val = (q_val.exp() * vrange).sum(2)

                    actions = q_val.argmax(1).tolist()

                    for i in range(len(actions)):
                        if random.random() < self.epsilon:
                            actions[i] = np.random.randint(0, 5)

            # take action in env
            next_obs_pos, r, done, _ = self.env.step(actions)
        

            # return data and update observation

            buffer.add(q_val.numpy(), actions, r, next_obs_pos)


            if done == False and self.env.steps < self.max_steps:

                obs_pos = next_obs_pos 
            else:
                # finish and send buffer
                if done:
                    buffer.finish()
                else:
                    with torch.no_grad():
                        q_val = self.model.step(torch.FloatTensor(next_obs_pos[0]), torch.FloatTensor(next_obs_pos[1]))
                        if self.distributional:
                            q_val = (q_val.exp() * vrange).sum(2)
                    buffer.finish(q_val)

                buffers.append(buffer)
                if len(buffers) == 8:
                    self.buffer.batch_add.remote(buffers)
                    buffers.clear()

                done = False
                self.model.reset()
                obs_pos = self.env.reset()

                imitation = True if random.random() < self.imitation_ratio else False
                if imitation:
                    imitation_actions = find_path(self.env)
                    while imitation_actions is None:
                        obs_pos = self.env.reset()
                        imitation_actions = find_path(self.env)

                    buffer = LocalBuffer(obs_pos, True)
                else:
                    # load weights from learner
                    weights_id = ray.get(self.learner.get_weights.remote())
                    weights = ray.get(weights_id)
                    self.model.load_state_dict(weights)

                    buffer = LocalBuffer(obs_pos, False)

        return self.id
        
    
