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
  -d/--daemon       -  daemon mode: poll docker for new services and register them
  -o/--once         -  run once: poll docker for new services and register them once
  -e/--env          -  environment
  --debug           -  enable debug output
"""

env = False
daemon = False
once = False
interactive = False
debug = False

try:
  opts, remainder = getopt.gnu_getopt(sys.argv[1:], "hidoe:", ["help", "interactive", "once", "daemon", "env=", "debug"])
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
  elif opt in ("-o", "--once"):
    once = "True"
  elif opt in ("-e", "--env"):
    env = arg
  elif opt in ("--debug"):
    debug = "True"

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

def comm(command_line):
        process = subprocess.Popen(shlex.split(command_line), stdout = subprocess.PIPE, stderr = subprocess.PIPE)
        out, error = process.communicate()
        return out

def poll_docker():
    j = AutoVivification()
    # get output from docker ps
    x = comm("docker ps").split("\n")
    # remove the 'title' line
    x.pop(0)
    for i in x:
      if i:
        # split apart lines on 'more than 2 whitespaces'
        y = re.split(r'\s{2,}', i)
        # so for repo/foo:latest, turn repo and latest into tags for name foo
        if env:
          tags = [env,]
        else:
          tags = []
        n1 = y[1].split("/")
        n2 = y[1].split(":")
        if n1[0]:
          tags.append(n1[0])
          tags.append("%s-%s" % (env,n1[0]))
        if n2[1]:
          tags.append(n2[1])
          tags.append("%s-%s" % (env,n2[1]))
        if debug:
          print "Tags:"
          print tags
        name = n1[1].split(":")[0]
	# pull a list of service port mappings
        # we need this to support multiple ports inside a container
        # if this doesn't exist, dockingstation will blow up and refues to register the service
        if debug:
           print "Trying: http://localhost:8500/v1/kv/service/%s/ports" % name
        r = requests.get("http://localhost:8500/v1/kv/service/%s/ports" % name)
        if r.content:
          if debug:
             print base64.b64decode(json.loads(r.content)[0]['Value'])
          ports = json.loads(base64.b64decode(json.loads(r.content)[0]['Value']))
        else:
          print "ERROR: please populate ports for service %s since none will show up under dockingstation!"
        # look for forwarded ports and create a consul service entry for each of them
        for p in y[5].split(","):
          m = re.match("(\d+\.+)+(\d:)(\d+)->(\d+)\/(\w+)", p.strip())
          if m != None:
            port =  p.strip().split(":")[1].split("->")
            dockerport = int(port[1].split("/")[0])
            if str(dockerport) not in ports.keys():
               print "ERROR: unable to find port %s in service map. Please provide port mappings. Ignoring service!" % str(dockerport)
               return []
            else:
               hostport = int(port[0])
               # now look for checks in kv
               # checks should map to: http://localhost:8500/v1/kv/checks/[name]
               # format maps to: https://www.consul.io/docs/agent/checks.html, minus the initial 'check' root
               # get a list of check
               # use jinja template to map to docker host port
               chk = requests.get("http://localhost:8500/v1/kv/service/%s/check" % name)
               if chk.content:
                   j['check'] = Environment().from_string(base64.b64decode(json.loads(chk.content)[0]['Value'])).render(checkport=hostport)
                   if debug:
                       print "DEBUG: found check:"
                       print j['check']
               if debug:
		   print "## PORT"
		   print p
		   print "Docker port: %s" % str(dockerport)
		   print "Host port: %s" % str(hostport)
               # We use the port kv map to create a unique name per port for the image
               j['name'] = "%s-%s" % (name, ports[str(dockerport)])
               j['tags'] = tags
               j['port'] = hostport
               if debug:
                 print j
               r = requests.post("http://127.0.0.1:8500/v1/agent/service/register", data=json.dumps(j))
               return r

if daemon:
    while True:
        r = poll_docker()
        if r:
            if r.status_code != 200:
                print "ERROR: request failed with status code %s" % str(r.status_code)
                print r.content
            else:
                if debug:
                    print r.content
        time.sleep(5)
elif once:
    r = poll_docker()
    if r:
        if r.status_code != 200:
            print "ERROR: request failed with status code %s" % str(r.status_code)
            print r.content
        else:
            if debug:
                print r.content
else:
  print "ERROR: no valid arguments specified!"
  usage()
  sys.exit(1)
