#!/usr/bin/env python
"""
This agent interacts with an RL-Glue environment to collect
a set of (s, a, r, absorb) experiences for batch reinforceement learning. 

This uses the skeleton_agent.py file from the Python-codec of rl-glue
as a starting point. 

Author: Nathan Sprague
"""


# 
# Copyright (C) 2008, Brian Tanner
# 
#http://rl-glue-ext.googlecode.com/
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import copy
import cPickle
import theano
from rlglue.agent.Agent import Agent
from rlglue.agent import AgentLoader as AgentLoader
from rlglue.types import Action
from rlglue.types import Observation
from rlglue.utils import TaskSpecVRLGLUE3
from pylearn2.training_algorithms import sgd
from pylearn2.models import mlp
from pylearn2.models import maxout
from pylearn2.space import Conv2DSpace
from pylearn2.termination_criteria import EpochCounter
import neural_qlearn_dataset as nqd
import time
import theano.tensor as T
import temp_mlp

from random import Random

import numpy as np

import matplotlib.pyplot as plt
import time

class NeuralQLearnAgent(Agent):
    randGenerator=Random()

    def agent_init(self,task_spec_string):
        """ 
        This function is called once at the beginning of an experiment.

        Arguments: task_spec_string - A string defining the task.  This string
                                      is decoded using 
                                      TaskSpecVRLGLUE3.TaskSpecParser
        """
        self.start_time = time.time()
        self.image = None
        self.show_ale = False
        self.total_reward = 0
        self.mini_batch_size = 32
        self.num_mini_batches = 1
        self.frame_count = 0
        self.frames_trained = 0
        self.qvalue_sum = 0
        self.qvalue_count = 0
        self.learning_rate = .00001
        momentum_factor = 0.9
        self.testing_policy = False
        self.epoch_counter = 0
        self.epochs_until_test = 5
        self.policy_test_file_name = "results.csv"
        load_file = False
        load_file_name = "cnnparams.pkl"
        self.save_file_name = "cnnparams.pkl"
        self.lower_learning_rate = .000000001
        self.anneal_length = 650000

        
        #starting value for epsilon-greedy
        self.epsilon = 1 

        TaskSpec = TaskSpecVRLGLUE3.TaskSpecParser(task_spec_string)
        if TaskSpec.valid:
            
            assert ((len(TaskSpec.getIntObservations())== 0) != \
                (len(TaskSpec.getDoubleObservations()) == 0 )), \
                "expecting continous or discrete observations.  Not both."
            assert len(TaskSpec.getDoubleActions())==0, \
                "expecting no continuous actions"
            assert not TaskSpec.isSpecial(TaskSpec.getIntActions()[0][0]), \
                " expecting min action to be a number not a special value"
            assert not TaskSpec.isSpecial(TaskSpec.getIntActions()[0][1]), \
                " expecting max action to be a number not a special value"
            self.num_actions=TaskSpec.getIntActions()[0][1]+1


        self.num_actions = 3
        self.int_states = len(TaskSpec.getIntObservations()) > 0

        # Create neural network and initialize trainer and dataset
        
        if load_file:
            thefile = open(load_file_name, "r")
            
            self.cnn = cPickle.load(thefile)
        else:
        
            self.first_conv_layer = maxout.MaxoutConvC01B(16, 1, (8, 8), (1, 1), 
                            (1, 1), "first conv layer", irange=.1, 
                                            kernel_stride=(4, 4), min_zero=True)
                                            
            self.second_conv_layer = maxout.MaxoutConvC01B(32, 1, (4, 4), 
                            (1, 1), (1, 1), "second conv layer", irange=.01, 
                                            kernel_stride=(2, 2), min_zero=True)
                                            
            self.rect_layer = mlp.RectifiedLinear(dim=256, 
                            layer_name="rectified layer", irange=.1)
                            
            self.output_layer = mlp.Linear(self.num_actions, "output layer", 
                            irange=.1)

            layers = [self.first_conv_layer, self.second_conv_layer, 
                            self.rect_layer, self.output_layer]

            self.cnn = mlp.MLP(layers, input_space = Conv2DSpace((80, 80), 
                                    num_channels=4, axes=('c', 0, 1, 'b')), 
                                    batch_size=self.mini_batch_size)

        self.data = nqd.NeuralQLearnDataset(self.cnn, mini_batch_size = self.mini_batch_size, 
                                            num_mini_batches = self.num_mini_batches, 
                                            learning_rate=self.learning_rate, 
                                            momentum_step_size=momentum_factor)

        #Create appropriate RL-Glue objects for storing these.
        self.last_action=Action()
        self.last_observation=Observation()

        if not load_file:
            thefile = open(self.policy_test_file_name, "w")
            thefile.write("Reward, Average predicted Q value, Episode frames, Current epsilon value, Frames trained, Current learning rate\n")
            thefile.close()



    def agent_start(self,observation):
        """  
        This method is called once at the beginning of each episode. 
        No reward is provided, because reward is only available after
        an action has been taken. 

        Arguments: 
           observation - An observation of type rlglue.types.Observation

        Returns: 
           An action of type rlglue.types.Action
        """

        self.start_time = time.time()
        this_int_action = self.get_action()
        return_action=Action()
        return_action.intArray = [this_int_action]


        self.last_action = copy.deepcopy(return_action)
        self.last_observation = copy.deepcopy(observation)

        return return_action
        
    def get_action(self):
        """
        Get predictions on the last state to get the next action from qvalues.
        Uses epsilon-greedy.
        """
        if self.testing_policy:
            epsilon = .05
        else:
            epsilon = self.epsilon
            
            if len(self.data) > 10000:
                self.epsilon -= (.000001 * self.num_mini_batches)
                if (self.epsilon < .1):
                    self.epsilon = .1
                
        if self.randGenerator.random() < epsilon or len(self.data) <= 7:
            val = self.randGenerator.randint(0,self.num_actions-1)
        else:
            state = self.data.get_state(len(self.data) - 1).astype('float32').reshape((1, 4, 80, 80))
            
            state /= 128.0
            
            state = self.data.dimshuf_func(state)

            qvalues = self.data.fprop_func(state)
            
            qvalues = qvalues.tolist()
            
            val = qvalues[0].index(max(qvalues[0]))
            
            self.qvalue_sum += max(qvalues[0])
            self.qvalue_count += 1
        
        val = self.get_action_val(val)

        return val
        
    def get_action_val(self, val):
        if val == 0:
            return 0
        elif val == 1:
            return 11
        elif val == 2:
            return 12
            
    def get_val_action(self, val):
        if val == 0:
            return 0
        elif val == 11:
            return 1
        elif val == 12:
            return 2

    def _show_ale_color(self):
        """
        Show an arcade learning environment screen.
        """
        plt.ion()
        img = np.array(\
            self.last_observation.intArray).reshape((160, 210, 3))
        img = np.transpose(img, (1,0,2))
        if self.image == None:
            self.image = plt.imshow(img/256.0, interpolation='none')
            fig = plt.gcf()
            fig.set_size_inches(4,3)
            plt.show()
        else:
            self.image.set_data(img/256.0) 
        plt.draw()
        time.sleep(.0)

    def _show_ale_gray(self):
        """
        Show an arcade learning environment screen.
        """
        plt.ion()
        img = np.array(\
            self.last_observation.intArray).reshape((80, 105))
        img = np.transpose(img)
        if self.image == None:
            self.image = plt.imshow(img/256.0, interpolation='none', \
                                            cmap="gray")
            fig = plt.gcf()
            fig.set_size_inches(4,3)
            plt.show()
        else:
            self.image.set_data(img/256.0)
        plt.draw()
        time.sleep(.0)

    def agent_step(self, reward, observation):
        """
        This method is called each time step. 

        Arguments: 
           reward      - Real valued reward.
           observation - An observation of type rlglue.types.Observation

        Returns: 
           An action of type rlglue.types.Action
        
        """
        self.total_reward += reward
        
        #set reward to be either -1, 0, or 1 as described in atari paper
        if reward > 0:
            reward = 1
		
        if reward < 0:
            reward = -1
        
        self.frame_count += 1
        
        self.data.add(self.last_observation.intArray, \
                        self.get_val_action(self.last_action.intArray[0]), 
                        reward, False)

        #if self.frames_trained > 50000:
           # phi = (self.frames_trained - 50000) / self.anneal_length
           # self.data.learning_rate = self.learning_rate - ((self.learning_rate - 
            #                            self.lower_learning_rate) * phi)
        
        if len(self.data) > 10000 and not self.testing_policy:
            self.frames_trained += 1
            self.data.train()
        
        this_int_action = self.get_action()
        return_action = Action()
        return_action.intArray = [this_int_action]
        
        if self.show_ale:
            #self._show_ale_color()
            self._show_ale_gray()

        self.last_action = copy.deepcopy(return_action)
        self.last_observation = copy.deepcopy(observation)

        return return_action

    def agent_end(self, reward):
        """
        This function is called once at the end of an episode. 
        
        Arguments: 
           reward      - Real valued reward.

        Returns: 
            None
        """
        self.total_reward += reward

        if reward > 0:
            reward = 1
		
        if reward < 0:
            reward = -1
        
        if self.testing_policy :
            #print the reward for this policy
            thefile = open(self.policy_test_file_name, "a")
            
            thefile.write(str(self.total_reward) +  ", ")
            thefile.write(str(self.qvalue_sum / self.qvalue_count) + ", ")
            thefile.write(str(self.frame_count) + ", ")
            thefile.write(str(self.epsilon) + ", ")
            thefile.write(str(self.frames_trained) + ", ")
            thefile.write(str(self.data.learning_rate.get_value()) + "\n")
            thefile.close()

            self.data.remove(self.frame_count)
        
        self.total_reward = 0
        self.qvalue_sum = 0
        self.qvalue_count = 0
        self.frame_count = 0
        self.save_params(self.save_file_name)
        
        self.data.add(self.last_observation.intArray, 
                        self.get_val_action(self.last_action.intArray[0]), 
                        reward, True)
                        
        if self.testing_policy:
            self.epoch_counter = 0
            self.testing_policy = False
        else:
            self.epoch_counter += 1
            if len(self.data) < 10000:
                self.epoch_counter = 0
            elif self.epoch_counter == self.epochs_until_test:
                self.testing_policy = True

    def agent_cleanup(self):
        """
        Called once at the end of an experiment.  We could save results
        here, but we use the agent_message mechanism instead so that
        a file name can be provided by the experiment.
        """
        pass
        
    def save_params(self, filename="cnnparams.pkl"):
        the_file = open(filename, "w")
        
        cPickle.dump(self.cnn, the_file, -1)

    def agent_message(self, in_message):
        """
        The experiment will cause this method to be called.  Used
        to save data to the indicated file. 
        """
        if in_message.startswith("save_data"):
            total_time = time.time() - self.start_time
            file_name=in_message.split(" ")[1]
            the_file = open(file_name, "w")
            all_data = (self.cnn.get_params())
            print "PICKLING: " + file_name
            #cPickle.dump(all_data, the_file, -1)
            #print "Simulated at a rate of {}/s".format(len(self.rewards) / 
            #                                           total_time)
            return "File saved successfully"

        else:
            return "I don't know how to respond to your message"


if __name__=="__main__":
    AgentLoader.loadAgent(NeuralQLearnAgent())
