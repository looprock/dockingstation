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
import hashlib

def usage():
  print sys.argv[0] + """ [options] [appname]
  options:

  -h/--help         -  print usage summary
  -i/--interactive  -  interactive mode: passthrough to docker CLI (not implemented yet)
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
  # checks should map to: http://localhost:8500/v1/kv/checks/[name]
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

def poll_docker():
  rval = AutoVivification()
  j = AutoVivification()
  # get output from docker ps
  x = comm("docker ps").split("\n")
  bug([x])
  # remove the 'title' line
  x.pop(0)
  bug(x)
  for i in x:
    bug([i])
    if i:
      # split apart lines on 'more than 2 whitespaces'
      y = re.split(r'\s{2,}', i)
      bug([y])
      # so for repo/foo:latest, turn repo and latest into tags for name foo
      if environment:
        tags = [environment,]
      else:
        tags = []
      n1 = y[1].split("/")
      n2 = y[1].split(":")
      if n1[0]:
        tags.append(n1[0])
        if environment:
          tags.append("%s-%s" % (environment,n1[0]))
      if n2[1]:
        tags.append(n2[1])
        if environment:
          tags.append("%s-%s" % (environment,n2[1]))
      bug(["tags", tags])
      name = n1[1].split(":")[0]
      # pull a list of service port mappings
      # we need this to support multiple ports inside a container
      # if this doesn't exist, dockingstation will blow up and refues to register the service
      bug(["Trying: http://localhost:8500/v1/kv/service/%s/ports" % name])
      r = requests.get("http://localhost:8500/v1/kv/service/%s/ports" % name)
      if r.content:
        bug([base64.b64decode(json.loads(r.content)[0]['Value'])])
        ports = json.loads(base64.b64decode(json.loads(r.content)[0]['Value']))
      else:
        print "ERROR: please populate ports for service %s since none will show up under dockingstation!"
        # look for forwarded ports and create a consul service entry for each of them
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
            if n2[1]:
              j['name'] = "%s-%s" % (j['name'], n2[1])
            j['tags'] = tags
            j['port'] = hostport
            bug(["Final Payload:", j])
            jsum = hashlib.md5(str(j)).hexdigest()
            if jsum in checksums:
              msg = "I've seen checksum %s before, not updating" % jsum
              bug([msg])
              rval['name']['status'] = 'registered'
              rval['name']['content'] = msg
            else:
              bug(["Registering check for %s, checksum %s" % (j['name'], jsum)])
              r = requests.post("http://127.0.0.1:8500/v1/agent/service/register", data=json.dumps(j))
              bug([r.status_code, r.content])
              checksums.append(jsum)
              rval['name']['status'] = 'new'
              rval['name']['content'] = r
  return rval

if daemon:
  while True:
    r = poll_docker()
    if r:
      for i in r:
        if r[i]['status'] == 'new' and r[i]['content'].status_code != 200:
            print "ERROR: request for %s failed with status code %s" % str(r[i]['name'],r[i]['content'].status_code)
            print r[i]['content'].content
        else:
          bug(["SUCCESSfully posted!", r[i]['status'], r[i]['content']])
    else:
      print "ERROR: nothing returned in r."
    time.sleep(5)
elif once:
    # NOTE: do not use this mode! You won't maintain the checksum DB and every time your run it you'll incur a
    # 'downtime' in consul registration. There seems to be intermittence issues with consul-template that will
    # cause you to lose your servers from the list.
    r = poll_docker()
    if r:
      for i in r:
        if r[i]['status'] == 'new' and r[i]['content'].status_code != 200:
            print "ERROR: request for %s failed with status code %s" % str(r[i]['name'],r[i]['content'].status_code)
            print r[i]['content'].content
        else:
          bug(["SUCCESSfully posted!", r[i]['status'], r[i]['content']])
    else:
        print "ERROR: nothing returned in r."
else:
  print "ERROR: no valid arguments specified!"
  usage()
  sys.exit(1)
