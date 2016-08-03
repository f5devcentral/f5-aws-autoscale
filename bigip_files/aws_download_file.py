#!/usr/bin/python

import sys
import os
import base64
import datetime
import hashlib
import hmac
import requests
import json
from optparse import OptionParser
from urlparse import urlparse

'''
Simple quick and dirty utility to download a file, using credentials from ENVIRONMENT:

Mostly Adapted from:
https://docs.aws.amazon.com/general/latest/gr/sigv4-signed-request-examples.html

	* If from remote URL, uses basic or no authentication
	* If from AWS (S3), uses AWS credentials
		NOTE: If using in prod, it's a much better idea to use AWS::CloudFormation::Authentication: 
		https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-authentication.html
		However, using it to keep flexibility and dependencies down in example CFTs

'''


def sign(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def getSignatureKey(key, dateStamp, regionName, serviceName):
    kDate = sign(('AWS4' + key).encode('utf-8'), dateStamp)
    kRegion = sign(kDate, regionName)
    kService = sign(kRegion, serviceName)
    kSigning = sign(kService, 'aws4_request')
    return kSigning

def usage_detailed():
    print "OPTIONS:"
    print "		-d 	<cluster_members>.	ex. \"bigip-1.example.com,bigip-2.example.com,bigip-3.example.com\""
    print "		-u 	--username     username if using basic auth"
    print "		-p  --password     password if using basic auth"
    print "		-r 	--region       aws_region"
    print "		--aws_acccess_key  aws_acccess_key"
    print "		--aws_secret_key   aws_secret_key"
    print ""
    print "USAGE: "
    print "	ex. " + sys.argv[0] + "-d \"/config/ssl/ssl.key\" https://s3.amazonaws.com/f5example/website.pfx\""


usage = "usage: " + str(sys.argv[0]) + " [options] download_url"
parser = OptionParser(usage=usage)
parser.add_option("-d", "--download_dir", action="store", type="string", dest="download_dir", help="download directory. ex. /config/ssl/ssl.key" )  
parser.add_option("-u", "--username", action="store", type="string", dest="username", help="username if using basic auth" )
parser.add_option("-p", "--password", action="store", type="string", dest="password", help="password if using basic auth" )
parser.add_option("-r", "--region", action="store", type="string", dest="region", help="aws region" )
parser.add_option("--aws_access_key", action="store", type="string", dest="aws_access_key", help="aws_access_key" )
parser.add_option("--aws_secret_key", action="store", type="string", dest="aws_secret_key", help="aws_secret_key" )
parser.add_option("-l", "--debug_logging", action="store", type="string", dest="debug_logging", default=False, help="debug logging: True or False" )          
(options, args) = parser.parse_args()

#PARAMS
method = 'GET'
username = ""
password = ""

if args:
	parsed_url = urlparse(args[0])
else:
	usage_detailed()
	sys.exit()


if options.download_dir:
	download_dir = options.download_dir
else:
	download_dir = "/var/tmp"

# Basic or No Auth
if options.username:
	username = options.username
else:
	username = os.environ.get('ADMIN_USERNAME')

if options.password:
	password = options.password
else:
	password = os.environ.get('ADMIN_PASSWORD')

if options.username and not options.password:
	print "If username is provided, password is also required."
	usage_detailed()
	sys.exit(0)


# Simple check to see if S3 URL
if "s3" in parsed_url.hostname:
	service = 's3'
	host = parsed_url.hostname
	canonical_uri = parsed_url.path
	# not parsing request params for now
	request_parameters = ''
	endpoint = parsed_url.scheme + "://" + str(host)
	s3_path  = parsed_url.path.strip("/")
	elements = s3_path.split("/")
	bucket_name = elements.pop(0)
	object_name = "/".join(elements)

	print "S3 bucket_name: " + str(bucket_name) 
	print "S3 object_name: " + str(object_name)

	if options.region:
		region = options.region
	else:
		# S3 Bucket generally in Region Big-IP is making call from
		with open('/shared/vadc/aws/iid-document') as f:
			iid = json.loads(f.read())
		region = iid['region']

	if options.aws_access_key:
		access_key = options.aws_access_key
		secret_key = options.aws_secret_key
	else:
		access_key = os.environ.get('IAM_ACCESS_KEY')
		secret_key = os.environ.get('IAM_SECRET_KEY')


	if access_key is None or secret_key is None:
		print 'No access key is available.'
		sys.exit()

	t = datetime.datetime.utcnow()
	amzdate = t.strftime('%Y%m%dT%H%M%SZ')
	datestamp = t.strftime('%Y%m%d') # Date w/o time, used in credential scope
	canonical_querystring = request_parameters
	canonical_headers = 'host:' + host + '\n' + 'x-amz-content-sha256:UNSIGNED-PAYLOAD' + '\n' + 'x-amz-date:' + amzdate + '\n'
	signed_headers = 'host;x-amz-content-sha256;x-amz-date'
	payload_hash = 'UNSIGNED-PAYLOAD'
	canonical_request = method + '\n' + canonical_uri + '\n' + canonical_querystring + '\n' + canonical_headers + '\n' + signed_headers + '\n' + payload_hash
	algorithm = 'AWS4-HMAC-SHA256'
	credential_scope = datestamp + '/' + region + '/' + service + '/' + 'aws4_request'
	string_to_sign = algorithm + '\n' +  amzdate + '\n' +  credential_scope + '\n' +  hashlib.sha256(canonical_request).hexdigest()
	signing_key = getSignatureKey(secret_key, datestamp, region, service)
	signature = hmac.new(signing_key, (string_to_sign).encode('utf-8'), hashlib.sha256).hexdigest()
	authorization_header = algorithm + ' ' + 'Credential=' + access_key + '/' + credential_scope + ', ' +  'SignedHeaders=' + signed_headers + ', ' + 'Signature=' + signature
	headers = {'x-amz-date':amzdate, 'x-amz-content-sha256': 'UNSIGNED-PAYLOAD', 'Authorization':authorization_header}
	request_url = endpoint + canonical_uri

	print '\nBEGIN REQUEST++++++++++++++++++++++++++++++++++++'
	print 'Request URL = ' + request_url
	r = requests.get(request_url, headers=headers)

	print '\nRESPONSE++++++++++++++++++++++++++++++++++++'
	print 'Response code: %d\n' % r.status_code

else:

	print '\nBEGIN REQUEST++++++++++++++++++++++++++++++++++++'
	print 'Request URL = ' + parsed_url.geturl()
	r = requests.get(parsed_url.geturl(), auth=('username', 'password')) 

	print '\nRESPONSE++++++++++++++++++++++++++++++++++++'
	print 'Response code: %d\n' % r.status_code

# Save to File

if r.content:	

	filename = os.path.basename(parsed_url.path)

	with open( download_dir + "/" + filename , 'w') as f:
	    f.write(r.content)

	print "Downloaded to: " + download_dir + "/" + filename 
else:
	print "No valid content retrieved"

