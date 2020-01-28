"""TD3-compatible multi-agent feedforward policy."""
import tensorflow as tf
import numpy as np
from functools import reduce

from hbaselines.fcnet.td3 import FeedForwardPolicy
from hbaselines.multi_fcnet.base import MultiFeedForwardPolicy as BasePolicy
from hbaselines.multi_fcnet.replay_buffer import MultiReplayBuffer
from hbaselines.utils.tf_util import get_trainable_vars
from hbaselines.utils.tf_util import reduce_std


class MultiFeedForwardPolicy(BasePolicy):
    """TD3-compatible multi-agent feedforward neural.

    The attributes described in this docstring are only used if the `maddpg`
    parameter is set to True. The attributes are dictionaries of their
    described form for each agent if `shared` is set to False.

    See the docstring of the parent class for a further description of this
    class.

    Attributes
    ----------
    noise : float
        scaling term to the range of the action space, that is subsequently
        used as the standard deviation of Gaussian noise added to the action if
        `apply_noise` is set to True in `get_action`
    target_policy_noise : float
        standard deviation term to the noise from the output of the target
        actor policy. See TD3 paper for more.
    target_noise_clip : float
        clipping term for the noise injected in the target actor policy
    replay_buffer : hbaselines.multi_fcnet.replay_buffer.MultiReplayBuffer
        the replay buffer for each agent
    terminals1 : tf.compat.v1.placeholder
        placeholder for the next step terminals for each agent
    rew_ph : tf.compat.v1.placeholder
        placeholder for the rewards for each agent
    action_ph : tf.compat.v1.placeholder
        placeholder for the actions for each agent
    obs_ph : tf.compat.v1.placeholder
        placeholder for the observations for each agent
    obs1_ph : tf.compat.v1.placeholder
        placeholder for the next step observations for each agent
    all_obs_ph : tf.compat.v1.placeholder
        placeholder for the last step full state observations
    all_obs1_ph : tf.compat.v1.placeholder
        placeholder for the current step full state observations
    all_action_ph : tf.compat.v1.placeholder
        placeholder for the actions of all agents
    actor_tf : tf.Variable
        the output from the actor network
    critic_tf : list of tf.Variable
        the output from the critic networks. Two networks are used to stabilize
        training.
    actor_target : tf.Variable
        the output from a noisy version of the target actor network
    critic_loss : tf.Operation
        the operation that returns the loss of the critic
    critic_optimizer : tf.Operation
        the operation that updates the trainable parameters of the critic
    target_init_updates : tf.Operation
        an operation that sets the values of the trainable parameters of the
        target actor/critic to match those actual actor/critic
    target_soft_updates : tf.Operation
        soft target update function
    actor_loss : tf.Operation
        the operation that returns the loss of the actor
    actor_optimizer : tf.Operation
        the operation that updates the trainable parameters of the actor
    """

    def __init__(self,
                 sess,
                 ob_space,
                 ac_space,
                 co_space,
                 buffer_size,
                 batch_size,
                 actor_lr,
                 critic_lr,
                 verbose,
                 tau,
                 gamma,
                 layer_norm,
                 layers,
                 act_fun,
                 use_huber,
                 noise,
                 target_policy_noise,
                 target_noise_clip,
                 shared,
                 maddpg,
                 all_ob_space=None,
                 n_agents=1,
                 scope=None,
                 zero_fingerprint=False,
                 fingerprint_dim=2):
        """Instantiate a multi-agent feed-forward neural network policy.

        Parameters
        ----------
        sess : tf.compat.v1.Session
            the current TensorFlow session
        ob_space : gym.spaces.*
            the observation space of the environment
        ac_space : gym.spaces.*
            the action space of the environment
        co_space : gym.spaces.*
            the context space of the environment
        buffer_size : int
            the max number of transitions to store
        batch_size : int
            SGD batch size
        actor_lr : float
            actor learning rate
        critic_lr : float
            critic learning rate
        verbose : int
            the verbosity level: 0 none, 1 training information, 2 tensorflow
            debug
        tau : float
            target update rate
        gamma : float
            discount factor
        layer_norm : bool
            enable layer normalisation
        layers : list of int or None
            the size of the Neural network for the policy
        act_fun : tf.nn.*
            the activation function to use in the neural network
        use_huber : bool
            specifies whether to use the huber distance function as the loss
            for the critic. If set to False, the mean-squared error metric is
            used instead
        noise : float
            scaling term to the range of the action space, that is subsequently
            used as the standard deviation of Gaussian noise added to the
            action if `apply_noise` is set to True in `get_action`.
        target_policy_noise : float
            standard deviation term to the noise from the output of the target
            actor policy. See TD3 paper for more.
        target_noise_clip : float
            clipping term for the noise injected in the target actor policy
        shared : bool
            whether to use a shared policy for all agents
        maddpg : bool
            whether to use an algorithm-specific variant of the MADDPG
            algorithm
        all_ob_space : gym.spaces.*
            the observation space of the full state space. Used by MADDPG
            variants of the policy.
        n_agents : int
            the number of agents in the networks. This is needed if using
            MADDPG with a shared policy to compute the length of the full
            action space. Otherwise, it is not used.
        scope : str
            an upper-level scope term. Used by policies that call this one.
        zero_fingerprint : bool
            whether to zero the last two elements of the observations for the
            actor and critic computations. Used for the worker policy when
            fingerprints are being implemented.
        fingerprint_dim : bool
            the number of fingerprint elements in the observation. Used when
            trying to zero the fingerprint elements.
        """
        # Instantiate a few terms (needed if MADDPG is used).
        if shared:
            # action magnitudes
            ac_mag = 0.5 * (ac_space.high - ac_space.low)

            self.noise = noise * ac_mag
            self.target_policy_noise = np.array([ac_mag * target_policy_noise])
            self.target_noise_clip = np.array([ac_mag * target_noise_clip])
        else:
            self.noise = {}
            self.target_policy_noise = {}
            self.target_noise_clip = {}
            for key in ac_space.keys():
                # action magnitudes
                ac_mag = 0.5 * (ac_space[key].high - ac_space[key].low)

                self.noise[key] = noise * ac_mag
                self.target_policy_noise[key] = \
                    np.array([ac_mag * target_policy_noise])
                self.target_noise_clip[key] = \
                    np.array([ac_mag * target_noise_clip])

        # variables to be initialized later (if MADDPG is used)
        self.replay_buffer = None
        self.terminals1 = None
        self.rew_ph = None
        self.action_ph = None
        self.obs_ph = None
        self.obs1_ph = None
        self.all_obs_ph = None
        self.all_obs1_ph = None
        self.all_action_ph = None
        self.actor_tf = None
        self.critic_tf = None
        self.actor_target = None
        self.critic_loss = None
        self.critic_optimizer = None
        self.target_init_updates = None
        self.target_soft_updates = None
        self.actor_loss = None
        self.actor_optimizer = None

        super(MultiFeedForwardPolicy, self).__init__(
            sess=sess,
            ob_space=ob_space,
            ac_space=ac_space,
            co_space=co_space,
            buffer_size=buffer_size,
            batch_size=batch_size,
            actor_lr=actor_lr,
            critic_lr=critic_lr,
            verbose=verbose,
            tau=tau,
            gamma=gamma,
            layer_norm=layer_norm,
            layers=layers,
            act_fun=act_fun,
            use_huber=use_huber,
            shared=shared,
            maddpg=maddpg,
            all_ob_space=all_ob_space,
            n_agents=n_agents,
            base_policy=FeedForwardPolicy,
            scope=scope,
            zero_fingerprint=zero_fingerprint,
            fingerprint_dim=fingerprint_dim,
            additional_params=dict(
                noise=noise,
                target_policy_noise=target_policy_noise,
                target_noise_clip=target_noise_clip,
            ),
        )

    def _setup_maddpg(self, scope):
        """See setup."""
        # Create an input placeholder for the full state observations.
        self.all_obs_ph = tf.compat.v1.placeholder(
            tf.float32,
            shape=(None,) + self.all_ob_space.shape,
            name='all_obs')

        self.all_obs1_ph = tf.compat.v1.placeholder(
            tf.float32,
            shape=(None,) + self.all_ob_space.shape,
            name='all_obs1')

        if self.shared:
            # Create an input placeholder for the full actions.
            self.all_action_ph = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None,) + (self.all_ob_space.shape[0] * self.n_agents,),
                name='all_actions')

            # Create actor and critic networks for the shared policy.
            replay_buffer, terminals1, rew_ph, action_ph, obs_ph, \
                obs1_ph, actor_tf, critic_tf, noisy_actor_target = \
                self._setup_agent(
                    ob_space=self.ob_space,
                    ac_space=self.ac_space,
                    co_space=self.co_space,
                    target_policy_noise=self.target_policy_noise,
                    target_noise_clip=self.target_noise_clip,
                )

            # Store the new objects in their respective attributes.
            self.replay_buffer = replay_buffer
            self.terminals1 = terminals1
            self.rew_ph = rew_ph
            self.action_ph = action_ph
            self.obs_ph = obs_ph
            self.obs1_ph = obs1_ph
            self.actor_tf = actor_tf
            self.critic_tf = critic_tf
            self.actor_target = noisy_actor_target

            # Create an input placeholder for the full next step actions. Used
            # when computing the critic target.
            self.all_action1_ph = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None,) + (self.all_ob_space.shape[0] * self.n_agents,),
                name='all_actions')

            # Setup the target critic and critic update procedure.
            loss, optimizer = self._setup_critic_update(
                critic=self.critic_tf,
                actor_target=self.all_action1_ph,
                rew_ph=self.rew_ph,
                done1=self.terminals1,
                scope=scope,
            )
            self.critic_loss = loss
            self.critic_optimizer = optimizer

            # Create the target update operations.
            init, soft = self._setup_target_updates(
                'model', 'target', scope, self.tau, self.verbose)
            self.target_init_updates = init
            self.target_soft_updates = soft

            # Setup the actor update procedure.
            loss, optimizer = self._setup_actor_update(
                combined_actors=self.all_action_ph,  # FIXME
                scope=scope,
            )
            self.actor_loss = loss
            self.actor_optimizer = optimizer

            # Setup the running means and standard deviations of the model
            # inputs and outputs.
            self._setup_stats(
                rew_ph=self.rew_ph,
                actor_loss=self.actor_loss,
                critic_loss=self.critic_loss,
                critic_tf=self.critic_tf,
                actor_tf=self.actor_tf
            )
        else:
            # Create an input placeholder for the full actions.
            all_ac_dim = sum(self.ac_space[key].shape[0]
                             for key in self.ac_space.keys())

            self.all_action_ph = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None, all_ac_dim),
                name='all_actions')

            self.replay_buffer = {}
            self.terminals1 = {}
            self.rew_ph = {}
            self.action_ph = {}
            self.obs_ph = {}
            self.obs1_ph = {}
            self.actor_tf = {}
            self.critic_tf = {}
            self.actor_target = {}
            actors = []
            actor_targets = []

            # We move through the keys in a sorted fashion so that we may
            # collect the observations and actions for the full state in a
            # sorted manner as well.
            for key in sorted(self.ob_space.keys()):
                # Create actor and critic networks for the the individual
                # policies.
                with tf.compat.v1.variable_scope(key, reuse=False):
                    replay_buffer, terminals1, rew_ph, action_ph, obs_ph, \
                        obs1_ph, actor_tf, critic_tf, noisy_actor_target = \
                        self._setup_agent(
                            ob_space=self.ob_space[key],
                            ac_space=self.ac_space[key],
                            co_space=self.co_space[key],
                            target_policy_noise=self.target_policy_noise[key],
                            target_noise_clip=self.target_noise_clip[key],
                        )

                # Store the new objects in their respective attributes.
                self.replay_buffer[key] = replay_buffer
                self.terminals1[key] = terminals1
                self.rew_ph[key] = rew_ph
                self.action_ph[key] = action_ph
                self.obs_ph[key] = obs_ph
                self.obs1_ph[key] = obs1_ph
                self.actor_tf[key] = actor_tf
                self.critic_tf[key] = critic_tf
                self.actor_target[key] = noisy_actor_target
                actors.append(actor_tf)
                actor_targets.append(noisy_actor_target)

            # Combine all actors for when creating a centralized differentiable
            # critic.
            combined_actors = tf.concat(actors, axis=1)

            # Combine all actor targets to create a centralized target actor.
            noisy_actor_target = tf.concat(actor_targets, axis=1)

            # Now that we have all actor targets, we can start constructing
            # centralized critic targets and all update procedures.
            self.critic_loss = {}
            self.critic_optimizer = {}
            self.target_init_updates = {}
            self.target_soft_updates = {}
            self.actor_loss = {}
            self.actor_optimizer = {}

            for key in sorted(self.ob_space.keys()):
                # Append the key to the outer scope term.
                scope_i = key if scope is None else "{}/{}".format(scope, key)

                with tf.compat.v1.variable_scope(key, reuse=False):
                    # Setup the target critic and critic update procedure.
                    loss, optimizer = self._setup_critic_update(
                        critic=self.critic_tf[key],
                        actor_target=noisy_actor_target,
                        rew_ph=self.rew_ph[key],
                        done1=self.terminals1[key],
                        scope=scope_i
                    )
                    self.critic_loss[key] = loss
                    self.critic_optimizer[key] = optimizer

                    # Create the target update operations.
                    init, soft = self._setup_target_updates(
                        'model',  'target', scope_i, self.tau, self.verbose)
                    self.target_init_updates[key] = init
                    self.target_soft_updates[key] = soft

                    # Setup the actor update procedure.
                    loss, optimizer = self._setup_actor_update(
                        combined_actors=combined_actors, scope=scope_i)
                    self.actor_loss[key] = loss
                    self.actor_optimizer[key] = optimizer

                    # Setup the running means and standard deviations of the
                    # model inputs and outputs.
                    self._setup_stats(
                        rew_ph=self.rew_ph[key],
                        actor_loss=self.actor_loss[key],
                        critic_loss=self.critic_loss[key],
                        critic_tf=self.critic_tf[key],
                        actor_tf=self.actor_tf[key]
                    )

    def _setup_agent(self,
                     ob_space,
                     ac_space,
                     co_space,
                     target_policy_noise,
                     target_noise_clip):
        """Create the components for an individual agent.

        Parameters
        ----------
        ob_space : gym.spaces.*
            the observation space of the individual agent
        ac_space : gym.spaces.*
            the action space of the individual agent
        co_space : gym.spaces.*
            the context space of the individual agent
        target_policy_noise : TODO
            TODO
        target_noise_clip : TODO
            TODO

        Returns
        -------
        MultiReplayBuffer
            the replay buffer object for each agent
        tf.compat.v1.placeholder
            placeholder for the next step terminals for each agent
        tf.compat.v1.placeholder
            placeholder for the rewards for each agent
        tf.compat.v1.placeholder
            placeholder for the actions for each agent
        tf.compat.v1.placeholder
            placeholder for the observations for each agent
        tf.compat.v1.placeholder
            placeholder for the next step observations for each agent
        tf.Variable
            the output from the actor network
        list of tf.Variable
            the output from the critic networks. Two networks are used to
            stabilize training.
        tf.Variable
            the output from a noise target actor network
        """
        # Compute the shape of the input observation space, which may include
        # the contextual term.
        ob_dim = self._get_ob_dim(ob_space, co_space)

        # =================================================================== #
        # Step 1: Create a replay buffer object.                              #
        # =================================================================== #

        replay_buffer = MultiReplayBuffer(
            buffer_size=self.buffer_size,
            batch_size=self.batch_size,
            obs_dim=ob_dim[0],
            ac_dim=ac_space.shape[0],
            all_obs_dim=self.all_obs_ph.shape[-1],
            all_ac_dim=self.all_action_ph.shape[-1],
            shared=self.shared,
            n_agents=self.n_agents,
        )

        # =================================================================== #
        # Step 2: Create input variables.                                     #
        # =================================================================== #

        with tf.compat.v1.variable_scope("input", reuse=False):
            terminals1 = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None, 1),
                name='terminals1')
            rew_ph = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None, 1),
                name='rewards')
            action_ph = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None,) + ac_space.shape,
                name='actions')
            obs_ph = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None,) + ob_dim,
                name='obs0')
            obs1_ph = tf.compat.v1.placeholder(
                tf.float32,
                shape=(None,) + ob_dim,
                name='obs1')

        # =================================================================== #
        # Step 3: Create actor and critic variables.                          #
        # =================================================================== #

        with tf.compat.v1.variable_scope("model", reuse=False):
            actor_tf = self.make_actor(obs_ph, ac_space)
            critic_tf = [
                self.make_critic(self.all_obs_ph, self.all_action_ph,
                                 scope="centralized_qf_{}".format(i))
                for i in range(2)
            ]

        with tf.compat.v1.variable_scope("target", reuse=False):
            # create the target actor policy
            actor_target = self.make_actor(obs1_ph, ac_space)

            # smooth target policy by adding clipped noise to target actions
            target_noise = tf.random.normal(
                tf.shape(actor_target), stddev=target_policy_noise)
            target_noise = tf.clip_by_value(
                target_noise, -target_noise_clip, target_noise_clip)

            # clip the noisy action to remain in the bounds
            noisy_actor_target = tf.clip_by_value(
                actor_target + target_noise,
                ac_space.low,
                ac_space.high
            )

        return replay_buffer, terminals1, rew_ph, action_ph, obs_ph, obs1_ph, \
            actor_tf, critic_tf, noisy_actor_target

    def make_actor(self, obs, ac_space, reuse=False, scope="pi"):
        """Create an actor tensor.

        Parameters
        ----------
        obs : tf.compat.v1.placeholder
            the input observation placeholder of the individual agent
        ac_space : gym.space.*
            the action space of the individual agent
        reuse : bool
            whether or not to reuse parameters
        scope : str
            the scope name of the actor

        Returns
        -------
        tf.Variable
            the output from the actor
        """
        with tf.compat.v1.variable_scope(scope, reuse=reuse):
            pi_h = obs

            # create the hidden layers
            for i, layer_size in enumerate(self.layers):
                pi_h = self._layer(
                    pi_h,  layer_size, 'fc{}'.format(i),
                    act_fun=self.act_fun,
                    layer_norm=self.layer_norm
                )

            # create the output layer
            policy = self._layer(
                pi_h, ac_space.shape[0], 'output',
                act_fun=tf.nn.tanh,
                kernel_initializer=tf.random_uniform_initializer(
                    minval=-3e-3, maxval=3e-3)
            )

            # scaling terms to the output from the policy
            ac_means = (ac_space.high + ac_space.low) / 2.
            ac_magnitudes = (ac_space.high - ac_space.low) / 2.

            policy = ac_means + ac_magnitudes * tf.to_float(policy)

        return policy

    def make_critic(self, obs, action, reuse=False, scope="qf"):
        """Create a critic tensor.

        Parameters
        ----------
        obs : tf.compat.v1.placeholder
            the input observation placeholder
        action : tf.compat.v1.placeholder
            the input action placeholder
        reuse : bool
            whether or not to reuse parameters
        scope : str
            an outer scope term

        Returns
        -------
        tf.Variable
            the output from the critic
        """
        with tf.compat.v1.variable_scope(scope, reuse=reuse):
            # concatenate the observations and actions
            qf_h = tf.concat([obs, action], axis=-1)

            # create the hidden layers
            for i, layer_size in enumerate(self.layers):
                qf_h = self._layer(
                    qf_h,  layer_size, 'fc{}'.format(i),
                    act_fun=self.act_fun,
                    layer_norm=self.layer_norm
                )

            # create the output layer
            qvalue_fn = self._layer(
                qf_h, 1, 'qf_output',
                kernel_initializer=tf.random_uniform_initializer(
                    minval=-3e-3, maxval=3e-3)
            )

        return qvalue_fn

    def _setup_critic_update(self, critic, actor_target, rew_ph, done1, scope):
        """Create the critic loss and optimization process.

        Parameters
        ----------
        critic : tf.Variable
            the output from the centralized critic of the agent
        actor_target : tf.Variable
            the output from the combined target actors of all agents
        rew_ph : tf.compat.v1.placeholder
            placeholder for the rewards of the agent
        done1 : tf.compat.v1.placeholder
            placeholder for the done mask of the agent
        scope : str
            an outer scope term

        Returns
        -------
        tf.Operation
            the operation that returns the loss of the critic
        tf.Operation
            the operation that updates the trainable parameters of the critic
        """
        if self.verbose >= 2:
            print('setting up critic optimizer')

        # Create the centralized target critic policy.
        with tf.compat.v1.variable_scope("target", reuse=False):
            critic_target = [
                self.make_critic(self.all_obs1_ph, actor_target,
                                 scope="qf_{}".format(i))
                for i in range(2)
            ]

        # compute the target critic term
        with tf.compat.v1.variable_scope("loss", reuse=False):
            q_obs1 = tf.minimum(critic_target[0], critic_target[1])
            target_q = tf.stop_gradient(
                rew_ph + (1. - done1) * self.gamma * q_obs1)

            tf.compat.v1.summary.scalar('critic_target',
                                        tf.reduce_mean(target_q))

        # choose the loss function
        if self.use_huber:
            loss_fn = tf.compat.v1.losses.huber_loss
        else:
            loss_fn = tf.compat.v1.losses.mean_squared_error

        critic_loss = [loss_fn(q, target_q) for q in critic]

        critic_optimizer = []

        for i, loss in enumerate(critic_loss):
            scope_name = 'model/qf_{}/'.format(i)
            if scope is not None:
                scope_name = scope + '/' + scope_name

            if self.verbose >= 2:
                critic_shapes = [var.get_shape().as_list()
                                 for var in get_trainable_vars(scope_name)]
                critic_nb_params = sum([reduce(lambda x, y: x * y, shape)
                                        for shape in critic_shapes])
                print('  critic shapes: {}'.format(critic_shapes))
                print('  critic params: {}'.format(critic_nb_params))

            # create an optimizer object
            optimizer = tf.compat.v1.train.AdamOptimizer(self.critic_lr)

            # create the optimizer object
            critic_optimizer.append(optimizer.minimize(
                loss=loss,
                var_list=get_trainable_vars(scope_name)))

        return critic_loss, critic_optimizer

    def _setup_actor_update(self, combined_actors, scope):
        """Create the actor loss and optimization process.

        Parameters
        ----------
        combined_actors : tf.Variable
            the output from all actors, as a function of the agent's policy
            parameters
        scope : str
            an outer scope term

        Returns
        -------
        tf.Operation
            the operation that returns the loss of the actor
        tf.Operation
            the operation that updates the trainable parameters of the actor
        """
        if self.verbose >= 2:
            print('setting up actor optimizer')

        scope_name = 'model/pi/'
        if scope is not None:
            scope_name = scope + '/' + scope_name

        if self.verbose >= 2:
            actor_shapes = [var.get_shape().as_list()
                            for var in get_trainable_vars(scope_name)]
            actor_nb_params = sum([reduce(lambda x, y: x * y, shape)
                                   for shape in actor_shapes])
            print('  actor shapes: {}'.format(actor_shapes))
            print('  actor params: {}'.format(actor_nb_params))

        # Create a differentiable form of the critic.
        with tf.compat.v1.variable_scope("model", reuse=False):
            critic_with_actor_tf = [
                self.make_critic(
                    self.all_obs_ph, combined_actors,
                    scope="centralized_qf_{}".format(i), reuse=True)
                for i in range(2)
            ]

        # compute the actor loss
        actor_loss = -tf.reduce_mean(critic_with_actor_tf[0])

        # create an optimizer object
        optimizer = tf.compat.v1.train.AdamOptimizer(self.actor_lr)

        actor_optimizer = optimizer.minimize(
            loss=actor_loss,
            var_list=get_trainable_vars(scope_name))

        return actor_loss, actor_optimizer

    @staticmethod
    def _setup_stats(rew_ph, actor_loss, critic_loss, actor_tf, critic_tf):
        """Prepare tensorboard logging for attributes of the agent.

        Parameters
        ----------
        rew_ph : tf.compat.v1.placeholder
            a placeholder for the rewards of an agent
        actor_loss : tf.Operation
            the operation that returns the loss of the actor
        critic_loss : list of tf.Operation
            the operation that returns the loss of the critic
        actor_tf : tf.Variable
            the output from the actor of the agent
        critic_tf : list of tf.Variable
            the output from the critics of the agent
        """
        # rewards
        tf.compat.v1.summary.scalar('rewards', tf.reduce_mean(rew_ph))

        # actor and critic losses
        tf.compat.v1.summary.scalar('actor_loss', actor_loss)
        tf.compat.v1.summary.scalar('Q1_loss', critic_loss[0])
        tf.compat.v1.summary.scalar('Q2_loss', critic_loss[1])

        # critic dynamics
        tf.compat.v1.summary.scalar(
            'reference_Q1_mean', tf.reduce_mean(critic_tf[0]))
        tf.compat.v1.summary.scalar(
            'reference_Q1_std', reduce_std(critic_tf[0]))

        tf.compat.v1.summary.scalar(
            'reference_Q2_mean', tf.reduce_mean(critic_tf[1]))
        tf.compat.v1.summary.scalar(
            'reference_Q2_std', reduce_std(critic_tf[1]))

        # actor dynamics
        tf.compat.v1.summary.scalar(
            'reference_action_mean', tf.reduce_mean(actor_tf))
        tf.compat.v1.summary.scalar(
            'reference_action_std', reduce_std(actor_tf))

    def _initialize_maddpg(self):
        """See initialize.

        This method initializes the target parameters to match the model
        parameters.
        """
        if self.shared:
            self.sess.run(self.target_init_updates)
        else:
            for key in self.target_init_updates.keys():
                self.sess.run(self.target_init_updates[key])

    def _update_maddpg(self, update_actor=True, **kwargs):
        """See update."""
        pass  # TODO

    def _get_action_maddpg(self, obs, context, apply_noise, random_actions):
        """See get_action."""
        actions = {}

        if random_actions:
            for key in obs.keys():
                # Get the action space of the specific agent.
                ac_space = self.ac_space if self.shared else self.ac_space[key]

                # Sample a random action.
                actions[key] = ac_space.sample()

        else:
            for key in obs.keys():
                # Get the action space of the specific agent.
                ac_space = self.ac_space if self.shared else self.ac_space[key]

                # Compute the deterministic action.
                action = self.sess.run(
                    self.actor_tf[key],
                    feed_dict={self.obs_ph[key]: obs[key]})

                if apply_noise:
                    # compute noisy action
                    if apply_noise:
                        action += np.random.normal(
                            0, self.noise[key], action.shape)

                    # clip by bounds
                    action = np.clip(action, ac_space.low, ac_space.high)

                actions[key] = action

        return actions

    def _value_maddpg(self, obs, context, action):
        """See value."""
        pass  # TODO

    def _store_transition_maddpg(self,
                                 obs0,
                                 context0,
                                 action,
                                 reward,
                                 obs1,
                                 context1,
                                 done,
                                 all_obs0,
                                 all_obs1,
                                 evaluate):
        """See store_transition."""
        pass  # TODO

    def _get_td_map_maddpg(self):
        """See get_td_map."""
        pass  # TODO