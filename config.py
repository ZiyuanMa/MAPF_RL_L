############################################################
####################    environment     ####################
############################################################
env_level = 0
map_length = 50
num_agents = 2
obs_radius = 4
reward_fn = dict(move=-0.075,
                stay_on_goal=0,
                stay_off_goal=-0.075,
                collision=-0.5,
                finish=3)

obs_shape = (6,9,9)


############################################################
####################         DQN        ####################
############################################################

# basic training setting
training_times = 1000000
save_interval=2500
gamma=0.99
batch_size=256
learning_starts=50000
target_network_update_freq=2500
save_path='./models'
max_steps = 256
bt_steps = 32
load_model = None

local_buffer_size = max_steps
global_buffer_size = 1024*local_buffer_size

actor_update_steps = 400

# gradient norm clipping
grad_norm_dqn=40

# n-step forward
forward_steps = 2


# prioritized replay
prioritized_replay_alpha=0.6
prioritized_replay_beta=0.4

# use double q learning
double_q = False

# adaptive learning
init_set = (1, 10)
max_num_agetns = 16
max_map_lenght = 40
pass_rate = 0.9

# dqn network setting
cnn_channel = 128
latent_dim = 256
