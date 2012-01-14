'''
Created on Dec 16, 2011

@author:     Prvn
@version:    0.2

This script basically wraps the Python boto API (2.0) and provides
the whole auto scale functionality at one place, making it easy 
to setup a auto scaling cluster.

TODO: Read configurations from both boto config file and user input

'''

import os
import ConfigParser
from argparse import ArgumentParser

from boto.ec2.connection import EC2Connection
from boto.ec2.autoscale import AutoScaleConnection
from boto.ec2.autoscale import AutoScalingGroup
from boto.ec2.autoscale import LaunchConfiguration
from boto.ec2.autoscale import ScalingPolicy
from boto.ec2.cloudwatch.metric import MetricAlarm
from boto.ec2.cloudwatch import CloudWatchConnection
from boto.ec2.elb import ELBConnection
from boto.ec2.elb import HealthCheck

class AutoScale:

    def __init__(self, args):
        """
        Initializing basic variables needed for auto scaling
        """
        self.configs                = ConfigParser.RawConfigParser()
        self.args                   = args
        self.test_props             = {}
        self.props                  = {}
        self.ec2_connection         = EC2Connection(self.args.access_key, self.args.secret_key)
        self.autoscale_connection   = AutoScaleConnection(self.args.access_key, self.args.secret_key)
        self.elb_connection         = ELBConnection(self.args.access_key, self.args.secret_key)
        self.cw_connection          = CloudWatchConnection(self.args.access_key, self.args.secret_key)
        self.firstInstance          = None
        self.launchConfiguration    = None
        self.healthCheck            = None

    def loadConfigs(self):
        """
        FIX ME: Currently doesnt do anything
        This method will load the configurations from boto config file if present else will 
        accept parameters passed by user.
        """
        if os.path.isfile("/etc/boto.cfg"):
            self.configs.read("/etc/boto.cfg")
            conf = self.configs.sections()
            self.populateConfigs(conf)
        if os.path.isfile("~/.boto"):
            self.configs.read("~/.boto")
            conf = self.configs.sections()
            self.populateConfigs(conf)
            
        print ">>> Loaded configs"
            
    def populateConfigs(self, sections):
        for section in sections:
            self.boto_props[section] = self.configs.items(section)
            for item in self.boto_props[section]:
                key, value = item
                if not self.props.has_key(key):
                    self.props[key] = value

    def createLaunchConfiguration(self, lc_name, ami_id, key_name):
        """
        Creates launch configuration for the auto scaling cluster
        """
        self.launchConfiguration = LaunchConfiguration(name     = lc_name, 
                                                       image_id = ami_id, 
                                                       key_name = key_name)
        self.autoscale_connection.create_launch_configuration(self.launchConfiguration)
        print ">>> Created launch configuration: " + lc_name

    def createAutoScaleGroup(self, asg_name):
        """
        Create a Auto scaling group for the auto scaling cluster
        """
        autoScalingGroup = AutoScalingGroup(group_name         = asg_name, 
                                            load_balancers     = [self.args.lb_name], 
                                            launch_config      = self.launchConfiguration, 
                                            min_size           = self.args.min_size, 
                                            max_size           = self.args.max_size, 
                                            availability_zones = ['us-east-1a'])
        self.autoscale_connection.create_auto_scaling_group(autoScalingGroup)
        print ">>> Created auto scaling group: " + asg_name

    def createTrigger(self, trigger_name, measure, asg_name):
        """
        Trigger to spawn new instances as per specific metrics
        """
        alarm_actions = []
        dimensions = {"AutoScalingGroupName" : asg_name}
        policies = self.autoscale_connection.get_all_policies(as_group=self.args.asg_name, policy_names=[self.args.asp_name])
        for policy in policies:
            alarm_actions.append(policy.policy_arn)
        alarm = MetricAlarm(name                = trigger_name, 
                            namespace           = "AWS/EC2", 
                            metric              = measure, 
                            statistic           = "Average", 
                            comparison          = ">=", 
                            threshold           = 50, 
                            period              = 60, 
                            unit                = "Percent",
                            evaluation_periods  = 2,  
                            alarm_actions       = alarm_actions, 
                            dimensions          = dimensions)
        
        self.cw_connection.create_alarm(alarm)
        print ">>> Created trigger: "+self.args.trigger

    def createAutoScalePolicy(self, asp_name):
        """
        Creates a Auto scaling policy to Add/Remove a instance from auto scaling cluster
        """
        self.autoScalingUpPolicy = ScalingPolicy(name                 = asp_name+'-up',
                                                 adjustment_type      = "ChangeInCapacity", 
                                                 as_name              = self.args.asg_name, 
                                                 scaling_adjustment   = 1, 
                                                 cooldown             = 180)
        self.autoScalingDownPolicy = ScalingPolicy(name                 = asp_name+'-down',
                                                   adjustment_type      = "ChangeInCapacity", 
                                                   as_name              = self.args.asg_name, 
                                                   scaling_adjustment   = -1, 
                                                   cooldown             = 180)

        self.autoscale_connection.create_scaling_policy(self.autoScalingUpPolicy)
        self.autoscale_connection.create_scaling_policy(self.autoScalingDownPolicy)
        
        print ">>> Created auto scaling policy: " + asp_name
    
    def configureHealthCheck(self, target):
        """
        Configures health check for the cluster
        """
        self.healthCheck = HealthCheck(target   = target, 
                                       timeout  = 5)
        print ">>> Configured health check for: " + target
        
    def createLoadBalancer(self, lb_name, region, lb_port, instance_port, protocol):
        """
        Creates a load balancer for cluster
        """
        listener = (int(lb_port), int(instance_port), protocol)
        tuple_list =[]
        tuple_list.append(listener)
        lbs = self.elb_connection.get_all_load_balancers()
        for lb in lbs:
            if lb.name != lb_name:
                self.elb_connection.create_load_balancer(lb_name, [region], tuple_list)
                self.elb_connection.configure_health_check(name         = lb_name, 
                                                           health_check = self.healthCheck)
                print ">>> Created load balancer: " + lb_name
            else:
                print "Load balancer with name '"+lb_name+"' already exists"
        
    def startInstance(self, image_id, key_name, region, instance_type):
        """
        Starts the first instance which will be serving requests irrespective of auto scaling 
        instances.
        """
        reservation = self.ec2_connection.run_instances(image_id=image_id, min_count=1, max_count=1, placement=region, key_name=key_name, instance_type=instance_type)
#        for instance in reservation.instances:
#            instance.add_tag('node', '0')
#            break

        self.firstInstance = reservation.instances[0].id.split('\'')[0]
        print ">>> Started instance: ", self.firstInstance
    
    def registerInstanceToELB(self, lb_name):
        """
        Register the first instance started to the Elastic Load Balancer.
        """
        self.elb_connection.register_instances(load_balancer_name = lb_name, 
                                               instances          = [self.firstInstance])
        print ">>> Registered instance '",self.firstInstance,"' to load balancer '"+lb_name+"'"
    
    def setUp(self):
        """
        Set's up the auto scaling for the application
        """
        # STEP 1: Load the configurations
        self.loadConfigs()
        # STEP 2: Configure the health check for the instances
        self.configureHealthCheck(self.args.lb_target)
        # STEP 3: Create a load balancer
        self.createLoadBalancer(self.args.lb_name, self.args.region, self.args.lb_port, self.args.instance_port, self.args.protocol)
        # STEP 4: Start the first instance
        self.startInstance(self.args.ami_id, self.args.key_name, self.args.region, self.args.instance_type)
        # STEP 5: Register the instance to the load balancer created in STEP 4
        self.registerInstanceToELB(self.args.lb_name)
        # STEP 6: Create launch configuration to launch instances by auto scale
        self.createLaunchConfiguration(self.args.lc_name, self.args.ami_id, self.args.key_name)
        # STEP 7: Create a auto scale group which will manage the instances started by auto scaling
        self.createAutoScaleGroup(self.args.asg_name)
        # STEP 8: Create a auto scaling policy to say add/remove a node
        self.createAutoScalePolicy(self.args.asp_name)
        # STEP 9: Create a trigger, so that auto scaling can trigger it to start 
        # or remove a instance from auto scaling group 
        self.createTrigger(self.args.trigger, self.args.measure, self.args.asg_name)
        

if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--access-key', metavar=('<access key>'), dest='access_key', required=True, help='AWS access key')
    parser.add_argument('--secret-key', metavar=('<secret key>'), dest='secret_key', required=True, help='AWS secret key')
    parser.add_argument('--ami-id', metavar=('<ami id>'), dest='ami_id', required=True, help='EC2 AMI ID')
    parser.add_argument('--key-name', metavar=('<key name>'), dest='key_name', required=True, help='EC2 key name')
    parser.add_argument('--region', metavar=('<region>'), dest='region', required=True, help='Region to launch instances in')
    parser.add_argument('--launch-config', metavar=('<launch configuration>'), dest='lc_name', required=True, help='Launch configuration name')
    parser.add_argument('--instance-type', metavar=('<t1.micro | m1.small | m1.large ..>'), dest='instance_type', required=True, help='Instance type')
    parser.add_argument('--load-balancer', metavar=('<load balancer>'), dest='lb_name', required=True, help='Load balancer name')
    parser.add_argument('--min-size', metavar=('<minimum size>'), dest='min_size', required=True, help='Minimum size of auto scale group')
    parser.add_argument('--max-size', metavar=('<maximum size>'), dest='max_size', required=True, help='Maximum size of auto scale group')
    parser.add_argument('--trigger', metavar=('<trigger name>'), dest='trigger', required=True, help='Trigger name')
    parser.add_argument('--measure', metavar=('<CPUUtilization>'), dest='measure', required=True, help='Measure name to watch for')
    parser.add_argument('--lb-target', metavar=('<load balancer target path>'), dest='lb_target', required=True, help='Load balancer target path to ping')
    parser.add_argument('--lb-port', metavar=('<load balancer port>'), dest='lb_port', required=True, help='Load balancer port')
    parser.add_argument('--instance-port', metavar=('<instance port>'), dest='instance_port', required=True, help='Instance port')
    parser.add_argument('--app-protocol', metavar=('<load balancer protocol>'), dest='protocol', required=True, help='Load balancer protocol')
    parser.add_argument('--auto-scaling-group', metavar=('<auto scaling group name>'), dest='asg_name', required=True, help='Name of auto scaling group to create')
    parser.add_argument('--auto-scaling-policy', metavar=('<auto scaling policy name>'), dest='asp_name', required=True, help='Name of auto scaling policy to create')
    
    args = parser.parse_args()
    
    autoscale = AutoScale(args)
    autoscale.setUp()
