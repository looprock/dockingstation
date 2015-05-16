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

def usage():
  print sys.argv[0] + """ [options] [appname]
  options:

  -h/--help         -  print usage summary
  -i/--interactive  -  interactive mode: passthrough to docker CLI (not implemented yet)
  -d/--daemon       -  daemon mode: poll docker for new services and register them
  -e/--env          -  environment
  --debug           -  enable debug output
"""

env = False
daemon = False
interactive = False
debug = False

try:
  opts, remainder = getopt.gnu_getopt(sys.argv[1:], "hide:", ["help", "interactive", "daemon", "env=", "debug"])
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

if daemon:
  while True:
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
        # now look for checks in kv
        # checks should map to: http://localhost:8500/v1/kv/checks/[name]
        # format maps to: https://www.consul.io/docs/agent/checks.html, minus the initial 'check' root
        # Example:
        #{
        #  "id": "api",
        #  "name": "HTTP API on port 5000",
        #  "http": "http://localhost:5000/health",
        #  "interval": "10s",
        #  "timeout": "1s"
        #}
        r = requests.get("http://localhost:8500/v1/kv/checks/%s" % name)
        if r.content:
          check = json.loads(base64.b64decode(json.loads(r.content)[0]['Value']))
        else:
          check = False
        if debug:
          print check
        # look for forwarded ports and create a consul service entry for each of them
        for p in y[5].split(","):
          m = re.match("(\d+\.+)+(\d:)(\d+)->(\d+)\/(\w+)", p.strip())
          if m != None:
            # putting the name here is bad, and if multiple ports are present this will overwrite the name
            # I will fix this when it's not 1am
            port =  int(p.strip().split(":")[1].split("->")[0])
            j['name'] = name
            j['tags'] = tags
            j['port'] = port
            if check:
              j['check'] = check
            if debug:
              print j
            r = requests.post("http://127.0.0.1:8500/v1/agent/service/register", data=json.dumps(j))
            if r.status_code != 200:
              print "ERROR: request failed with status code %s" % str(r.status_code)
              print r.content
              sys.exit(1)
    time.sleep(5)

else:
  print "ERROR: no valid arguments specified!"
  usage()
  sys.exit(1)