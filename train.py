import os
os.environ["OMP_NUM_THREADS"] = "1"
import torch
import numpy as np
import random

from worker import GlobalBuffer, Learner, Actor
import time
import ray
import threading

import config

torch.manual_seed(0)
np.random.seed(0)
random.seed(0)

if __name__ == '__main__':
    ray.init()

    buffer = GlobalBuffer.remote(2048)
    learner = Learner.remote(buffer)
    num_actors = 10
    actors = [Actor.remote(i, 0.4**(1+(i/(num_actors-1))*7), learner, buffer) for i in range(num_actors)]

    [ actor.run.remote() for actor in actors ]

    
    while not ray.get(buffer.ready.remote()):
        time.sleep(5)
        ray.get(learner.stats.remote(5))
        ray.get(buffer.stats.remote(5))

    print('start training')
    buffer.run.remote()
    learner.run.remote()
    
    done = False
    while not done:
        time.sleep(5)
        
        done = ray.get(learner.stats.remote(5))
        ray.get(buffer.stats.remote(5))
        print()
