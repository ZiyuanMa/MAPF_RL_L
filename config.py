############################################################
####################    environment     ####################
############################################################
env_level = 0
map_length = 20
num_agents = 6
obs_radius = 4
reward_fn = dict(move=-0.075,
                stay_on_goal=0,
                stay_off_goal=-0.125,
                collision=-0.5,
                finish=3)

obs_shape = (2,9,9)
pos_shape = (4,)


############################################################
####################         DQN        ####################
############################################################

# basic training setting
training_times = 1000000
save_interval=10000
gamma=0.99
batch_size=128
learning_starts=50000
target_network_update_freq=2000
save_path='./models'
max_steps = 8
bt_steps = 8
load_model = None

local_buffer_size = max_steps
global_buffer_size = 1024*local_buffer_size

actor_update_steps = 200

# gradient norm clipping
grad_norm_dqn=40

# distributional dqn
distributional = False

# prioritized replay
prioritized_replay_alpha=0.6
prioritized_replay_beta=0.4

# use double q learning
double_q = False

# adaptive learning
init_set = (4, 10)
max_num_agetns = 4
max_map_lenght = 50
pass_rate = 0.9

# dqn network setting
cnn_channel = 64
obs_dim = 2
obs_latent_dim = 240
pos_dim = 4
pos_latent_dim = 16

# communication
max_comm_agents = 3 # including agent itself, means one can at most communicate with (max_comm_agents-1) agents

# communication block
num_comm_layers = 2
num_comm_heads = 2
