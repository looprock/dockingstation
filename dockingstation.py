#!/usr/bin/env python
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
  print sys.argv[0] + """ [options] [appname]
  options:

  -h/--help         -  print usage summary
  -i/--interactive  -  interactive mode: passthrough to docker CLI (not implemented yet)
  -o/--once         -  non-daemon mode, run once and exit
  -d/--daemon       -  daemon mode: poll docker for new services and register them
  -e/--environment  -  environment
  --debug           -  enable debug output
"""

environment = False
daemon = False
once = False
interactive = False
debug = False

try:
  opts, remainder = getopt.gnu_getopt(sys.argv[1:], "hidoe:", ["help", "interactive", "once", "daemon", "environment=", "debug"])
except getopt.GetoptError:
  usage()
  sys.exit(2)
for opt, arg in opts:
  if opt in ("-h", "--help"):
    usage()
    sys.exit()
  elif opt in ("-i", "--interactive"):
    interactive = "True"
  elif opt in ("-d", "--daemon"):
    daemon = "True"
  # once is bad, do not use except for debugging. See comment above the section at the bottom
  elif opt in ("-o", "--once"):
    once = "True"
  elif opt in ("-e", "--environment"):
    environment = arg
  elif opt in ("--debug"):
    debug = "True"

checksums = []

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
  if debug:
    for i in msgs:
      print "DEBUG: %s" % (i)

def comm(command_line):
  process = subprocess.Popen(shlex.split(command_line), stdout = subprocess.PIPE, stderr = subprocess.PIPE)
  out, error = process.communicate()
  return out

def findchecks(name, service, hostport):
  # now look for checks in kv
  # checks should map to: http://localhost:8500/v1/kv/service/[name]/checks/[value to port map]
  # format maps to: https://www.consul.io/docs/agent/checks.html, minus the initial 'check' root
  # get a list of check
  # use jinja template to map to docker host port
  chk = requests.get("http://localhost:8500/v1/kv/service/%s/checks/%s" % (name,service))
  if chk.content:
    chk_tmpl = Environment().from_string(base64.b64decode(json.loads(chk.content)[0]['Value'])).render(checkport=hostport)
    rchk = json.loads(chk_tmpl)
    bug(["found check:", rchk])
    return rchk
  else:
    print "WARNING: no check found for %s %s: %s, you might want to create one" % (name, service, hostport)
    return False

def returnconsulself():
  r = requests.get("http://localhost:8500/v1/agent/self")
  if r.content:
    return r.json()
  else:
    return False

def deregister(consul_self, container):
  bug(["Trying to deregister services for container: %s" % container])
  url = 'http://localhost:8500/v1/kv/node/dockingstation/%s/%s' % (consul_self['Config']['NodeName'], container)
  errors = False
  g = requests.get(url)
  for i in json.loads(base64.b64decode(g.json()[0]['Value'])):
    # remove checks
    #{ "Datacenter": "dc1", "Node": "foobar", "CheckID": "service:redis1"}
    payload = {}
    # ask consul what my NodeName and Datacenter are
    payload['Node'] = consul_self['Config']['NodeName']
    payload['Datacenter'] = consul_self['Config']['Datacenter']
    payload['CheckID'] = "service:%s" % i
    r = requests.put("http://localhost:8500/v1/catalog/deregister", data=json.dumps(payload))
    bug(["Deregistering service : %s" % i, r.status_code, r.content])
    if r.status_code != 200:
      # we don't error here because technically we don't need checks
      #errors = True
      print "ERROR: unable to remove check %s RE container %s!" % (i, container)
    # remove services
    # payload example: {"Datacenter": "oakland", "Node": "docker1", "ServiceID": "docker"}
    payload = {}
    # ask consul what my NodeName and Datacenter are
    payload['Node'] = consul_self['Config']['NodeName']
    payload['Datacenter'] = consul_self['Config']['Datacenter']
    payload['ServiceID'] = i
    r = requests.put("http://localhost:8500/v1/catalog/deregister", data=json.dumps(payload))
    bug(["Deregistering service : %s" % i, r.status_code, r.content])
    if r.status_code != 200:
      errors = True
      print "ERROR: unable to remove service %s RE container %s!" % (i, container)
  if errors == False:
    d = requests.delete(url)
    if d.status_code == 200:
      print "Successfully removed all services for container %s!" % container
    else:
      print "ERROR: unable to remove reference for container %s!" % container
  else:
    print "ERROR: unable to remove all services for container %s!" % container

def getserviceports(name):
  # pull a list of service port mappings
  # we need this to support multiple ports inside a container
  # if this doesn't exist, dockingstation will blow up and refues to register the service
  bug(["Trying: http://localhost:8500/v1/kv/service/%s/ports" % name])
  r = requests.get("http://localhost:8500/v1/kv/service/%s/ports" % name)
  if r.content:
    bug([base64.b64decode(json.loads(r.content)[0]['Value'])])
    return json.loads(base64.b64decode(json.loads(r.content)[0]['Value']))
  else:
    return False

def getnodecontainers(consul_self):
  # r = requests.get("http://localhost:8500/v1/kv/node/dockingstation/docker2/?recurse")
  r = requests.get("http://localhost:8500/v1/kv/node/dockingstation/%s/?recurse" % consul_self['Config']['NodeName'])
  c = []
  if r.status_code == 200:
    for i in r.json():
      c.append(i['Key'].split("/")[-1])
    return c
    bug([c])
  else:
    print "WARNING: no state data returned for %s." % consul_self['Config']['NodeName']
    return []

def putnodeservices(consul_self, container, data):
  #data = {"780b57a94639": ["versiontest-service-v0-1"]}
  payload = json.dumps(data)
  url = "http://localhost:8500/v1/kv/node/dockingstation/%s/%s" % (consul_self['Config']['NodeName'], container)
  r = requests.put(url, data=payload)
  bug(["putnodeservices - %s : %s" % (r.status_code, url)])
  return r.status_code

def shipit():
  r = poll_docker()
  if r:
    bug(["R:", r])
    for i in r:
      if r[i]['status'] == 'registered':
        bug(["Nothing to see here, move along, already registered %s" % i])
      else:
        if r[i]['content'].status_code != 200:
          print "ERROR: request for %s failed with status code %s" % str(r[i]['name'],r[i]['content'].status_code)
          print r[i]['content'].content
        else:
          print "Successfully Registered: %s" % i 
          bug(["Successfully posted!", r[i]['status'], r[i]['content']])
  else:
    print "ERROR: nothing returned in r."

def poll_docker():
  service_data = {}
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
  x = comm("docker ps").split("\n")
  bug([x])
  # remove the 'title' line
  x.pop(0)
  bug(x)
  for i in x:
    ### process docker ps output
    bug([i])
    if i:
      services = []
      # split apart lines on 'more than 2 whitespaces'
      y = re.split(r'\s{2,}', i)
      current.append(y[0])
      bug([y])
      # so for repo/foo:latest, turn repo and latest into tags for name foo
      n = re.match("(\S+)\/(\S+):(\S+)", y[1])
      if n == None:
        print "ERROR: couldn't match the name of the docker image, something isn't right! Skipping!"
      else:
        tags = [n.group(1), n.group(3)]
        # liberally applying environment to tags
        if environment:
          tags = [environment,"%s-%s" % (environment,n.group(1)), "%s-%s" % (environment,n.group(3))]
        bug(["tags", tags])
        name = n.group(2)
        ports = getserviceports(name)
        if not ports:
          print "ERROR: please populate ports for service %s since nothing will show up under dockingstation otherwise! Skipping!" % name
        else:
          for p in y[5].split(","):
            m = re.match("(\d+\.+)+(\d:)(\d+)->(\d+)\/(\w+)", p.strip())
            if m != None:
              port =  p.strip().split(":")[1].split("->")
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
                bug([p, "Docker port: %s" % str(dockerport), "Host port: %s" % str(hostport)])
                # also add the version to the name
                j['name'] = "%s-%s" % (j['name'], n.group(3))
                j['tags'] = tags
                j['port'] = hostport
                bug(["Final Payload:", j])
                # get a list of containers I've already registered
                if y[0] in known:
                  msg = "I've seen container %s before, not updating" % y[0]
                  bug([msg])
                  rval[j['name']]['status'] = 'registered'
                  rval[j['name']]['content'] = msg
                else:
                  bug(["Registering container %s" % y[0]])
                  r = requests.put("http://127.0.0.1:8500/v1/agent/service/register", data=json.dumps(j))
                  bug([r.status_code, r.content])
                  rval[j['name']]['status'] = 'new'
                  rval[j['name']]['content'] = r
                  if r.status_code == 200:
                    services.append(j['name'])
      if services:
        putnodeservices(consul_self, y[0], services)
  # last but not least, if we had any containers disappear between runs, deregister the service from consul
  bug(["current", current, "known", known])
  for k in known:
    if k not in current:
      deregister(consul_self, k)
  return rval

if daemon:
  while True:
    shipit()
    time.sleep(5)
elif once:
  shipit()
else:
  print "ERROR: no valid arguments specified!"
  usage()
  sys.exit(1)