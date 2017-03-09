# deploy_stacks.py

"""
Short python script to deploy CloudFormation templates in this directory.
See README.md for usage.

Author: c.mutzel@f5.com, a.applebaum@f5.com

"""

import sys
import time
import json
import yaml
import ConfigParser
from optparse import OptionParser
import boto3


STACK_WAIT_TIMEOUT = 1800

class DeploymentManager:
    """Class to simplify management of a deployment composed of multiple CFTs"""

    def __init__(self, deployment_name, region):
        self.region = region
        self.deployment_name = deployment_name
        self.client = boto3.client('cloudformation', self.region)
        self.namespace = {}

    def get_stack_name(self, template_name):
        """Ensures our stack names are deterministic"""
        return '{}-{}'.format(self.deployment_name, template_name)

    def get_stack_status(self, stack_name):
        """Queries boto3 api for the stack status """
        stacks = self.client.list_stacks()['StackSummaries']
        for stack in stacks:
            if stack['StackName'].lower() == stack_name.lower():
                return stack
        return None

    def wait_for_stack_status(self, stack_name,
                              timeout=STACK_WAIT_TIMEOUT, poll_interval=10,
                              allowed_final_statuses=['CREATE_COMPLETE'],
                              allowed_wait_statuses=['CREATE_IN_PROGRESS']):
        """Waits for a stack to reach one of states in @allowed_final_status"""
        slept = 0
        while slept < timeout:
            stack_status = self.get_stack_status(stack_name)
            if not stack_status:
                raise Exception(("Could not retrieve status for {}, "
                                 "Does it exist?").format(stack_name))
            elif stack_status['StackStatus'] in allowed_final_statuses:
                return
            elif stack_status['StackStatus'] in allowed_wait_statuses:
                print ("Sleeping for {} seconds while stack "
                       "deployment completes.").format(poll_interval)
                time.sleep(poll_interval)
                slept += poll_interval
            else:
                raise Exception(("Fatal error while waiting for stack"
                                 "deployment, stack {} is in state {}").format(stack_name,
                                    stack_status['StackStatus']))
        raise Exception(("Timeout while waiting for stack deployment "
                         "to complete.  Last status was {}").format(stack_status))

    def get_stack_parameters(self, template_name):
        """Builds the inputs we need to deploy a stack in this deployment"""

        template_parameters = json.loads(self.read_template(template_name))['Parameters']
        stack_parameters = []
        for k in template_parameters.keys():
            # assign variables for those which we have in our 'namespace'
            if k in dm.namespace:
                new_param = {'ParameterKey': k, 'ParameterValue': dm.namespace[k]}
                stack_parameters.append(new_param)
        return stack_parameters

    def read_template(self, template_name):
        """Reads a CFT template from a file. Return whole template as string"""
        with open(TEMPLATES_DIR + '/' + template_name + '.template') as template_fp:
            return template_fp.read()

    def create_or_update_stack(self, template_name):
        """
            Deploys a stack from a given template.
            Does not update CF stacks if the template has changed.
        """
        stack_name = self.get_stack_name(template_name)
        stack_parameters = self.get_stack_parameters(template_name)
        template_body = self.read_template(template_name)

        # check if the stack exists
        status = self.get_stack_status(stack_name)

        # otherwise, deploy it
        if status and ( status['StackStatus'] == 'CREATE_COMPLETE' or 
                        status['StackStatus'] == 'UPDATE_COMPLETE'):
            pass
        elif not status or status['StackStatus'] in ['DELETE_COMPLETE']:
            create_response = self.client.create_stack(
                StackName=stack_name,
                #http://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_CreateStack.html
                #Capabilities.member.1=CAPABILITY_IAM,
                Capabilities=['CAPABILITY_IAM'],
                TemplateBody=template_body,
                Parameters=stack_parameters)
            self.wait_for_stack_status(stack_name)
        elif status['StackStatus'] in ['CREATE_IN_PROGRESS']:
            self.wait_for_stack_status(stack_name)
        else:
            raise Exception(
                'not sure what to do...stack is in state {}'.format(
                    status['StackStatus']))

        # keep track of variables that are outputs from each stack
        stack = self.describe_stack(template_name)
        self.add_outputs_to_namespace(stack)

        return stack

    def describe_stack(self, template_name):
        stack_name = self.get_stack_name(template_name)
        return self.client.describe_stacks(StackName=stack_name)['Stacks'][0]

    def add_vars_to_namespace(self, to_add):
        """
            Adds variables provided within the function arguments to the
            namespace for this deployment.
            Use to keep track of all variables for a deployment.
        """
        for k, v in to_add.iteritems():
            self.namespace[k] = v

    def add_outputs_to_namespace(self, stack):
        """
            Adds outputs of a stack to the
            namespace for this deployment.
            Use to keep track of all variables for a deployment.
        """
        if 'Outputs' in stack:
            for item in stack['Outputs']:
                self.namespace[item['OutputKey']] = item['OutputValue']


parser = OptionParser()
parser.add_option("-d", "--deployment", action="store", type="string", dest="deployment", help="deployment type" )
# Keeping so can load a custom file name or old .ini file
parser.add_option("-c", "--config", action="store", type="string", dest="config_file", help="Config file used" )
#parser.add_option("-r", "--regkey", action="store", type="string", dest="regkey", help="Registration Key for BYOL image" )
(options, args) = parser.parse_args()



deployment_type = "L7proxy-utility-only-immutable"


if options.deployment:
    deployment_type = options.deployment

TEMPLATES_DIR = "deployments/" + deployment_type + "/cfts"


# load variables provided by user. 
#Changing default to YAML but keeping config.ini for backwards compat.
config_file = "config.yaml"

if options.config_file:
    config_file = options.config_file


if ".yaml" in config_file:

    with open(config_file, 'r') as config_data:
        config = yaml.load(config_data)

    deployment_name = config['deploymentName']
    region = config['region']
    deploy_jmeter_host = config['deploy_jmeter_host']

    dm = DeploymentManager(deployment_name, region)
    dm.add_vars_to_namespace(config)


####################################

# CFT stacknames restricted 
# ex. stackName failed to satisfy constraint: Member must satisfy regular expression pattern: [a-zA-Z][-a-zA-Z0-9]* 
# so will construct stack names from file's base name

# define set of templates we are deploying
base_templates = ['common', 'application', 'autoscale-bigip' ]

if "byol" in deployment_type:
    base_templates.append('byol-bigip')
if deploy_jmeter_host:
    base_templates.append('ubuntu-client')

# #### BEGIN OUTPUT ####

print "Deployment Type: " + deployment_type 

# deploy all of the cloudformation templates
for template_name in base_templates:
    print 'Deploying {} template'.format(template_name)
    dm.create_or_update_stack(template_name)


if "byol" in deployment_type:

    # as a last step, add our byol instance to the ELB group
    # ideally should be done in CFTs, but requires significant refactoring
    print 'Adding BYOL BIG-IP instances to ElasticLoadBalancer group'
    elb_client = boto3.client('elb', region)

    elb_client.register_instances_with_load_balancer(
        LoadBalancerName= deployment_name + "-BigipElb",
        Instances=[{'InstanceId': dm.namespace['byolBigipInstance']}]
    )


output_list = ['bigipAutoscaleGroup']

if "sandwich" in deployment_type:
    output_list.append('bigipELBDnsName')
if "byol" in deployment_type:
    output_list.append('byolBigipInstance')
    output_list.append('byolBigipPublicIp')
if deploy_jmeter_host:
    output_list.append('clientPublicIP')

print 'Finished deployment of CFTs'
print 'Info:'
for k in output_list:
    print '  {}: {}'.format(k, dm.namespace[k])




