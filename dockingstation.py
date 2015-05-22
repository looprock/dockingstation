#!/usr/bin/env python
"""Discover docker containers and register them with consul"""
import subprocess
import shlex
import re
import sys
import json
import requests
import getopt
import time
import base64
from jinja2 import Environment

def usage():
  """Print usage"""
  print sys.argv[0] + """ [options] [appname]
  options:

  -h/--help         -  print usage summary
  -i/--interactive  -  interactive mode: passthrough to docker CLI (not implemented yet)
  -o/--once         -  non-daemon mode, run once and exit
  -d/--daemon       -  daemon mode: poll docker for new services and register them
  -e/--environment  -  environment
  --debug           -  enable debug output
"""

ENVIRONMENT = False
DAEMON = False
ONCE = False
INTERACTIVE = False
DEBUG = False

try:
  OPTS, REMAINDER = getopt.gnu_getopt(sys.argv[1:], "hidoe:", ["help", "interactive", "once", "daemon", "environment=", "debug"])
except getopt.GetoptError:
  usage()
  sys.exit(2)
for opt, arg in OPTS:
  if opt in ("-h", "--help"):
    usage()
    sys.exit()
  elif opt in ("-i", "--interactive"):
    INTERACTIVE = "True"
  elif opt in ("-d", "--daemon"):
    DAEMON = "True"
  # once is bad, do not use except for debugging. See comment above the section at the bottom
  elif opt in ("-o", "--once"):
    ONCE = "True"
  elif opt in ("-e", "--environment"):
    ENVIRONMENT = arg
  elif opt in "--debug":
    DEBUG = "True"

# generate nested python dictionaries, copied from here:
# http://stackoverflow.com/questions/635483/what-is-the-best-way-to-implement-nested-dictionaries-in-python
class AutoVivification(dict):
  """Implementation of perl's autovivification feature."""
  def __getitem__(self, item):
    try:
      return dict.__getitem__(self, item)
    except KeyError:
      value = self[item] = type(self)()
      return value

def bug(msgs):
  """Enable debug output"""
  if DEBUG:
    for i in msgs:
      print "DEBUG: %s" % (i)

def comm(command_line):
  """Return output of a system command"""
  process = subprocess.Popen(shlex.split(command_line), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  out, error = process.communicate()
  bug([error])
  return out

def findchecks(name, service, hostport):
  """Get a list of checks for a service you're registering with consul"""
  # now look for checks in kv
  # checks should map to: http://localhost:8500/v1/kv/service/[name]/checks/[value to port map]
  # format maps to: https://www.consul.io/docs/agent/checks.html, minus the initial 'check' root
  # get a list of check
  # use jinja template to map to docker host port
  chk = requests.get("http://localhost:8500/v1/kv/service/%s/checks/%s" % (name, service))
  if chk.content:
    chk_tmpl = Environment().from_string(base64.b64decode(json.loads(chk.content)[0]['Value'])).render(checkport=hostport)
    rchk = json.loads(chk_tmpl)
    bug(["found check:", rchk])
    return rchk
  else:
    print "WARNING: no check found for %s %s: %s, you might want to create one" % (name, service, hostport)
    return False

def returnconsulself():
  """Return the self information from the local consul agent"""
  response = requests.get("http://localhost:8500/v1/agent/self")
  if response.content:
    return response.json()
  else:
    return False

def deregister(consul_self, container):
  """Deregister all services and reference for a container in consul"""
  bug(["Trying to deregister services for container: %s" % container])
  url = 'http://localhost:8500/v1/kv/node/dockingstation/%s/%s' % (consul_self['Config']['NodeName'], container)
  errors = False
  containers = requests.get(url)
  for i in json.loads(base64.b64decode(containers.json()[0]['Value'])):
    checkid = "service:%s" % i
    response = requests.get("http://localhost:8500/v1/agent/check/deregister/%s" % checkid)
    bug(["Deregistering service : %s" % i, response.status_code, response.content])
    if response.status_code != 200:
      # we don't error here because technically we don't need checks
      #errors = True
      print "ERROR: unable to remove check %s RE container %s!" % (i, container)
    # remove services
    response = requests.put("http://localhost:8500/v1/agent/service/deregister/%s" % i)
    bug(["Deregistering service : %s" % i, response.status_code, response.content])
    if response.status_code != 200:
      errors = True
      print "ERROR: unable to remove service %s RE container %s!" % (i, container)
  if errors == False:
    delreq = requests.delete(url)
    if delreq.status_code == 200:
      print "Successfully removed all services for container %s!" % container
    else:
      print "ERROR: unable to remove reference for container %s!" % container
  else:
    print "ERROR: unable to remove all services for container %s!" % container

def getserviceports(name):
  """Return the port to service name map from consul"""
  # pull a list of service port mappings
  # we need this to support multiple ports inside a container
  # if this doesn't exist, dockingstation will blow up and refues to register the service
  bug(["Trying: http://localhost:8500/v1/kv/service/%s/ports" % name])
  response = requests.get("http://localhost:8500/v1/kv/service/%s/ports" % name)
  if response.content:
    bug([base64.b64decode(json.loads(response.content)[0]['Value'])])
    return json.loads(base64.b64decode(json.loads(response.content)[0]['Value']))
  else:
    return {}

def getnodecontainers(consul_self):
  """Return a list of containers registered with a consul node"""
  # r = requests.get("http://localhost:8500/v1/kv/node/dockingstation/docker2/?recurse")
  response = requests.get("http://localhost:8500/v1/kv/node/dockingstation/%s/?recurse" % consul_self['Config']['NodeName'])
  containers = []
  if response.status_code == 200:
    for i in response.json():
      containers.append(i['Key'].split("/")[-1])
    bug([containers])
    return containers
  else:
    print "WARNING: no state data returned for %s." % consul_self['Config']['NodeName']
    return []

def putnodeservices(consul_self, container, data):
  """Register a new container/service with consul"""
  #data = {"780b57a94639": ["versiontest-service-v0-1"]}
  payload = json.dumps(data)
  url = "http://localhost:8500/v1/kv/node/dockingstation/%s/%s" % (consul_self['Config']['NodeName'], container)
  response = requests.put(url, data=payload)
  bug(["putnodeservices - %s : %s" % (response.status_code, url)])
  return response.status_code

def shipit():
  """Output a response from poll_docker"""
  response = poll_docker()
  if response:
    bug(["R:", response])
    for i in response:
      if response[i]['status'] == 'registered':
        bug(["Nothing to see here, move along, already registered %s" % i])
      else:
        if response[i]['content'].status_code != 200:
          print "ERROR: request for %s failed with status code %s" % str(response[i]['name'], response[i]['content'].status_code)
          print response[i]['content'].content
        else:
          print "Successfully Registered: %s" % i
          bug(["Successfully posted!", response[i]['status'], response[i]['content']])
  else:
    print "ERROR: nothing returned in response."

def poll_docker():
  """Inspect docker containers and associated services and register them with consul if needed"""
  # get data about myself from consul
  consul_self = returnconsulself()
  if not consul_self:
    return False
  # also get a list of all my containers from previous run
  known = getnodecontainers(consul_self)
  # create a list for the containers in THIS run
  current = []
  rval = AutoVivification()
  j = AutoVivification()
  # get output from docker ps
  dockerps = comm("docker ps").split("\n")
  bug([dockerps])
  # remove the 'title' line
  dockerps.pop(0)
  bug(dockerps)
  for i in dockerps:
    ### process docker ps output
    bug([i])
    if i:
      services = []
      # split apart lines on 'more than 2 whitespaces'
      dockerparts = re.split(r'\s{2,}', i)
      current.append(dockerparts[0])
      bug([dockerparts])
      # so for repo/foo:latest, turn repo and latest into tags for name foo
      contname = re.match(r"(\S+)\/(\S+):(\S+)", dockerparts[1])
      if contname == None:
        print "ERROR: couldn't match the name of the docker image, something isn't right! Skipping!"
      else:
        tags = [contname.group(1), contname.group(3)]
        # liberally applying ENVIRONMENT to tags
        if ENVIRONMENT:
          tags = [ENVIRONMENT, "%s-%s" % (ENVIRONMENT, contname.group(1)), "%s-%s" % (ENVIRONMENT, contname.group(3))]
        bug(["tags", tags])
        name = contname.group(2)
        ports = getserviceports(name)
        if not ports:
          print "ERROR: please populate ports for service %s since nothing will show up under dockingstation otherwise! Skipping!" % name
        else:
          for portref in dockerparts[5].split(","):
            portmatch = re.match(r"(\d+\.+)+(\d:)(\d+)->(\d+)\/(\w+)", portref.strip())
            if portmatch != None:
              port = portref.strip().split(":")[1].split("->")
              dockerport = int(port[1].split("/")[0])
              # We use the port kv map to create a unique name per port for the image
              j['name'] = "%s-%s" % (name, ports[str(dockerport)])
              if str(dockerport) not in ports.keys():
                 print "ERROR: unable to find port %s in service map. Please provide port mappings. Ignoring service!" % str(dockerport)
              else:
                hostport = int(port[0])
                chk = findchecks(name, ports[str(dockerport)], hostport)
                if chk:
                  j['check'] = chk
                bug([portref, "Docker port: %s" % str(dockerport), "Host port: %s" % str(hostport)])
                # also add the version to the name
                j['name'] = "%s-%s" % (j['name'], contname.group(3))
                j['tags'] = tags
                j['port'] = hostport
                bug(["Final Payload:", j])
                # get a list of containers I've already registered
                if dockerparts[0] in known:
                  msg = "I've seen container %s before, not updating" % dockerparts[0]
                  bug([msg])
                  rval[j['name']]['status'] = 'registered'
                  rval[j['name']]['content'] = msg
                else:
                  bug(["Registering container %s" % dockerparts[0]])
                  response = requests.put("http://127.0.0.1:8500/v1/agent/service/register", data=json.dumps(j))
                  bug([response.status_code, response.content])
                  rval[j['name']]['status'] = 'new'
                  rval[j['name']]['content'] = response
                  if response.status_code == 200:
                    services.append(j['name'])
      if services:
        putnodeservices(consul_self, dockerparts[0], services)
  # last but not least, if we had any containers disappear between runs, deregister the service from consul
  bug(["current", current, "known", known])
  for k in known:
    if k not in current:
      deregister(consul_self, k)
  return rval

if DAEMON:
  while True:
    shipit()
    time.sleep(5)
elif ONCE:
  shipit()
else:
  print "ERROR: no valid arguments specified!"
  usage()
  sys.exit(1)
