from stable_baselines.common.input import observation_input
import numpy as np
import tensorflow as tf
from gym.spaces import Box
from hbaselines.models import build_fcnet, build_lstm
from hbaselines.utils.stats import normalize, denormalize


class FullyConnectedPolicy(object):
    """Policy object that implements a DDPG-like actor critic, using MLPs."""

    def __init__(self,
                 sess,
                 ob_space,
                 ac_space,
                 layers=None,
                 hidden_nonlinearity=tf.nn.relu,
                 output_nonlinearity=tf.nn.tanh,
                 obs_rms=None,
                 ret_rms=None,
                 observation_range=(-np.inf, np.inf),
                 return_range=(-np.inf, np.inf)):
        """Instantiate the policy model.

        Parameters
        ----------
        sess : tf.Session
            the tensorflow session
        ob_space : gym.spaces.Box
            shape of the observation space
        ac_space : gym.spaces.Box
            shape of the action space
        layers : list of int
            number of nodes in each hidden layer
        hidden_nonlinearity : tf.nn.*
            activation nonlinearity for the hidden nodes of the model
        output_nonlinearity : tf.nn.*
            activation nonlinearity for the output of the model
        """
        if layers is None:
            layers = [128, 128]

        # inputs
        self.sess = sess
        self.ob_space = ob_space
        self.ac_space = ac_space
        self.layers = layers
        self.hidden_nonlinearity = hidden_nonlinearity
        self.output_nonlinearity = output_nonlinearity
        self.obs_rms = obs_rms
        self.ret_rms = ret_rms
        self.observation_range = observation_range
        self.return_range = return_range

        # create placeholders for the observations and actions
        with tf.variable_scope('input', reuse=False):
            self.obs_ph, self.processed_x = observation_input(
                ob_space, batch_size=None, scale=False)
            self.action_ph = tf.placeholder(
                dtype=ac_space.dtype, shape=(None,) + ac_space.shape,
                name='action_ph')

        # create normalized variants of the observationss and actions
        self.normalized_obs_ph = tf.clip_by_value(
            normalize(self.processed_x, obs_rms),
            observation_range[0], observation_range[1])

        # variables that will be created by later methods
        self.policy = None
        self.normalized_critic_with_actor = None
        self.normalized_critic = None
        self.critic_with_actor = None
        self.critic = None

        # some assertions
        assert isinstance(ac_space, Box), \
            'Error: the action space must be of type gym.spaces.Box'
        assert np.all(np.abs(ac_space.low) == ac_space.high), \
            'Error: the action space low and high must be symmetric'
        assert len(layers) >= 1, \
            'Error: must have at least one hidden layer for the policy.'

    def make_actor(self, reuse=False, scope='pi'):
        """Create an actor tensor.

        Parameters
        ----------
        reuse : bool
            whether or not to reuse parameters
        scope : str
            the scope name of the actor

        Returns
        -------
        tuple of tf.Tensor
            the output tensor for the actor
        """
        self.policy = build_fcnet(
            inputs=tf.layers.flatten(self.normalized_obs_ph),
            num_outputs=self.ac_space.shape[0],
            hidden_size=self.layers,
            hidden_nonlinearity=self.hidden_nonlinearity,
            output_nonlinearity=self.output_nonlinearity,
            scope=scope,
            reuse=reuse
        )

        return self.policy

    def make_critic(self,
                    obs=None,
                    reuse=False,
                    scope='qf',
                    use_actor=False):
        """Create a critic tensor.

        Parameters
        ----------
        obs : tf.Tensor
            The observation placeholder (can be None for default placeholder)
        action : tf.Tensor
            The action placeholder (can be None for default placeholder)
        reuse : bool
            whether or not to reuse parameters
        scope : str
            the scope name of the critic

        Returns
        -------
        tf.Tensor
            the output tensor for the critic
        """
        if obs is None:
            obs = self.processed_x

        vf_h = tf.layers.flatten(obs)

        normalized_value_fn = build_fcnet(
            inputs=tf.concat([vf_h, self.action_ph], axis=-1),
            num_outputs=1,
            hidden_size=self.layers,
            hidden_nonlinearity=self.hidden_nonlinearity,
            output_nonlinearity=self.output_nonlinearity,
            scope=scope,
            reuse=False
        )

        self.normalized_critic = denormalize(
            tf.clip_by_value(normalized_value_fn,
                             self.return_range[0],
                             self.return_range[1]),
            self.ret_rms)

        self.critic = self.normalized_critic
        self._critic = self.normalized_critic[:, 0]

        if use_actor:
            self.normalized_critic_with_actor = build_fcnet(
                inputs=tf.concat([vf_h, self.policy], axis=-1),
                num_outputs=1,
                hidden_size=self.layers,
                hidden_nonlinearity=self.hidden_nonlinearity,
                output_nonlinearity=self.output_nonlinearity,
                scope=scope,
                reuse=True
            )

            critic_with_actor = denormalize(
                tf.clip_by_value(self.normalized_critic_with_actor,
                                 self.return_range[0],
                                 self.return_range[1]),
                self.ret_rms)

            self.critic_with_actor = critic_with_actor
            self._critic_with_actor = critic_with_actor[:, 0]
        else:
            self.critic_with_actor = None

        return self.critic, self.critic_with_actor

    def step(self, obs, state=None, mask=None):
        return self.sess.run(self.policy, {self.obs_ph: obs})

    def value(self, obs, action, state=None, mask=None, use_actor=False):
        if use_actor:
            return self.sess.run(
                self._critic_with_actor, feed_dict={self.obs_ph: obs})
        else:
            return self.sess.run(
                self._critic,
                feed_dict={self.obs_ph: obs, self.action_ph: action})


class LSTMPolicy(FullyConnectedPolicy):
    """Policy object that implements a DDPG-like actor critic, using LSTMs."""

    def __init__(self,
                 sess,
                 ob_space,
                 ac_space,
                 layers=None,
                 hidden_nonlinearity=tf.nn.relu,
                 output_nonlinearity=tf.nn.tanh,
                 obs_rms=None,
                 ret_rms=None,
                 observation_range=(-np.inf, np.inf),
                 return_range=(-np.inf, np.inf)):
        """See parent class."""
        if layers is None:
            layers = [64]

        assert len(layers) == 1, 'Only one LSTM layer is allowed.'

        super(LSTMPolicy, self).__init__(
            sess, ob_space, ac_space, layers, hidden_nonlinearity,
            output_nonlinearity, obs_rms, ret_rms, observation_range,
            return_range)

        # input placeholder to the internal states component of the actor
        self.states_ph = None

        # output internal states component from the actor
        self.state = None

        # initial internal state of the actor
        self.state_init = None

        # placeholders for the actor recurrent network
        self.train_length = tf.placeholder(dtype=tf.int32, name='train_length')
        self.batch_size = tf.placeholder(dtype=tf.int32, name='batch_size')

    def make_actor(self, obs=None, reuse=False, scope="pi"):
        """See parent class."""
        if obs is None:
            obs = self.processed_x

        # convert batch to sequence
        obs = tf.reshape(
            tf.layers.flatten(obs),
            [self.batch_size, self.train_length, self.ob_space.shape[0]]
        )

        # create the LSTM model
        self.policy, self.state_init, self.states_ph, self.state = build_lstm(
            inputs=obs,
            num_outputs=self.ac_space.shape[0],
            hidden_size=self.layers[0],
            scope=scope,
            reuse=reuse,
            nonlinearity=tf.nn.tanh,
        )

        return self.policy

    def step(self, obs, state=None, mask=None):
        """See parent class."""
        return self.sess.run(
            [self.policy, self.state],
            feed_dict={
                self.obs_ph: obs,
                self.states_ph: state,
                self.train_length: obs.shape[0],
                self.batch_size: obs.shape[0],
            }
        )

    def value(self, obs, action, state=None, mask=None):
        """See parent class."""
        return self.sess.run(self._value, {self.obs_ph: obs,
                                           self.action_ph: action})


# TODO
class FeudalPolicy(LSTMPolicy):
    """Policy object for the Feudal (FuN) hierarchical model.

    blank
    """
    pass


class HIROPolicy(LSTMPolicy):
    """Policy object for the HIRO hierarchical model.

    In this policy, we consider two actors and two critic, one for the manager
    and another for the worker.

    The manager receives state information and returns goals, while the worker
    receives the states and goals and returns an action. Moreover, while the
    worker performs actions at every time step, the manager only does so every
    c time steps (he is informed when to act from the algorithm).

    The manager is trained to maximize the total expected return from the
    environment, while the worker is rewarded to achieving states that are
    similar to those requested by the manager.

    See: https://arxiv.org/abs/1805.08296
    """

    def __init__(self,
                 sess,
                 ob_space,
                 ac_space,
                 layers=None,
                 hidden_nonlinearity=tf.nn.relu,
                 output_nonlinearity=tf.nn.tanh):
        if layers is None:
            layers = [64]

        assert len(layers) == 1, 'Only one LSTM layer is allowed.'

        super(HIROPolicy, self).__init__(
            sess, ob_space, ac_space, layers, hidden_nonlinearity,
            output_nonlinearity)

        # number of steps after which the manager performs an action
        self.c = 0  # FIXME

        # manager policy
        self.manager = None

        # worker policy
        self.worker = None

        # manager value function
        self.manager_vf = None
        self._manager_vf = None

        # worker value function
        self.worker_vf = None
        self._worker_vf = None

        # a placeholder for goals that are communicated to the worker
        self.goal_ph = tf.placeholder(dtype=tf.float32,
                                      shape=self.ob_space.shape[0])

        # a variable that internally stores the most recent goal to the worker
        self.cur_goal = None  # [0 for _ in range(ob_space.shape[0])]

        # hidden size of the actor LSTM  # TODO: add to inputs
        self.actor_size = 64

    def make_actor(self, obs=None, reuse=False, scope="pi"):
        """Create an actor object.

        Parameters
        ----------
        obs : tf.Tensor
            The observation placeholder (can be None for default placeholder)
        reuse : bool
            whether or not to reuse parameters
        scope : str
            the scope name of the actor

        Returns
        -------
        tuple of tf.Tensor
            the output tensor from the manager and the worker
        """
        if obs is None:
            obs = self.processed_x

        # input to the manager
        manager_in = tf.reshape(
            tf.layers.flatten(obs),
            [self.batch_size, self.train_length, self.ob_space.shape[0]]
        )

        # create the policy for the manager
        m_policy, m_state_init, m_states_ph, m_state = build_lstm(
            inputs=manager_in,
            num_outputs=self.ob_space.shape[0],
            hidden_size=self.actor_size,
            scope='manager_{}'.format(scope),
            reuse=reuse,
            nonlinearity=tf.nn.tanh,
        )

        # input to the worker
        worker_in = tf.reshape(
            tf.concat([tf.layers.flatten(obs), self.goal_ph], axis=-1),
            [self.batch_size, self.train_length, self.ob_space.shape[0]]
        )

        # create the policy for the worker
        w_policy, w_state_init, w_states_ph, w_state = build_lstm(
            inputs=worker_in,
            num_outputs=self.ac_space.shape[0],
            hidden_size=self.actor_size,
            scope='worker_{}'.format(scope),
            reuse=reuse,
            nonlinearity=tf.nn.tanh,
        )

        self.policy = (m_policy, w_policy)
        self.state_init = (m_state_init, w_state_init)
        self.states_ph = (m_states_ph, w_states_ph)
        self.state = (m_state, w_state)

        return self.policy

    def make_critic(self, obs=None, action=None, reuse=False, scope="qf"):
        """Create a critic object.

        Parameters
        ----------
        obs : tf.Tensor
            The observation placeholder (can be None for default placeholder)
        action : tf.Tensor
            The action placeholder (can be None for default placeholder)
        reuse : bool
            whether or not to reuse parameters
        scope : str
            the scope name of the critic

        Returns
        -------
        tuple of tf.Tensor
            the output tensor for the manager and the worker
        """
        if obs is None:
            obs = self.processed_x
        if action is None:
            action = self.action_ph

        vf_h = tf.layers.flatten(obs)

        # value function for the manager
        value_fn = build_fcnet(
            inputs=tf.concat([vf_h, self.goal_ph], axis=-1),
            num_outputs=1,
            hidden_size=[128, 128],
            hidden_nonlinearity=tf.nn.relu,
            output_nonlinearity=tf.nn.tanh,
            scope=scope,
            reuse=reuse,
            prefix='manager',
        )
        self.manager_vf = value_fn
        self._manager_vf = value_fn[:, 0]

        # value function for the worker
        value_fn = build_fcnet(
            inputs=tf.concat([vf_h, self.goal_ph, action], axis=-1),
            num_outputs=1,
            hidden_size=[128, 128],
            hidden_nonlinearity=tf.nn.relu,
            output_nonlinearity=tf.nn.tanh,
            scope=scope,
            reuse=reuse,
            prefix='worker',
        )
        self.worker_vf = value_fn
        self._worker_vf = value_fn[:, 0]

        self.value_fn = (self.manager_vf, self.worker_vf)

        return self.value_fn

    def step(self, obs, state=None, mask=None, **kwargs):
        """Return the policy for a single step.

        Parameters
        ----------
        obs : list of float or list of int
            The current observation of the environment
        state : list of float
            The last states (used in recurrent policies)
        mask : list of float
            The last masks (used in recurrent policies)

        Returns
        -------
        list of float
            the current goal from the manager
        list of float
            actions from the worker
        """
        if kwargs['apply_manager']:
            self.cur_goal = self.sess.run(
                self.manager,
                feed_dict={
                    self.obs_ph: obs,
                    self.states_ph: state[0]
                }
            )
        else:
            # TODO: the thing they do in the HIRO paper
            self.cur_goal = self.update_goal(self.cur_goal)

        action = self.sess.run(
            self.worker,
            feed_dict={
                self.obs_ph: obs,
                self.states_ph: state[1],
                self.goal_ph: self.cur_goal
            }
        )

        return self.cur_goal, action

    def value(self, obs, action, state=None, mask=None, **kwargs):
        """Return the value for a single step.

        Parameters
        ----------
        obs : list of float or list of int
            The current observation of the environment
        action : list of float or list of int
            The taken action
        state : list of float
            The last states (used in recurrent policies)
        mask : list of float
            The last masks (used in recurrent policies)

        Returns
        -------
        list of float
            The associated value of the goal from the manager
        list of float
            The associated value of the action from the worker
        """
        if kwargs['apply_manager']:
            v_manager = self.sess.run(self._manager_vf,
                                      feed_dict={self.obs_ph: obs,
                                                 self.goal_ph: self.cur_goal})
        else:
            v_manager = None

        v_worker = self.sess.run(self._worker_vf,
                                 feed_dict={self.obs_ph: obs,
                                            self.states_ph: state[1],
                                            self.goal_ph: self.cur_goal,
                                            self.action_ph: action})

        return v_manager, v_worker

    def update_goal(self, goal, obs, prev_obs):
        """Update the goal when the manager isn't issuing commands.

        Parameters
        ----------
        goal : list of float or np.ndarray
            previous step goals
        obs : list of float or np.ndarray
            observations from the current time step
        prev_obs : list of float or np.ndarray
            observations from the previous time step

        Returns
        -------
        list of float or list of int
            current step goals
        """
        return prev_obs + goal - obs
