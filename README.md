# dockingstation
A service to add  docker containers to consul for service discovery

# Options
<pre>
-h/--help         -  print usage summary
-i/--interactive  -  interactive mode: passthrough to docker CLI (not implemented yet)
-d/--daemon       -  daemon mode: poll docker for new services and register them
-e/--env          -  environment: this will add a tag inside consul for your environment
--debug           -  enable debug output
</pre>

# General functionality
This script will poll 'docker ps' for entries and add them to consul via API. It uses the image name to populate the consul service name (plus tags, see below) and parses forwarded ports to present in consul.

# daemon mode
This is currently the only available option. You can run this under something like supervisor as a 'background' process.

# How consul tags work in dockingstation
For repo/foo:latest, dockingstation would turn repo and latest into tags for name foo. You can also pass dockingstation -e to add an environment tag
To support multiple services you'll need to create a port map under: http://localhost:8500/v1/kv/service/[name]/ports that looks something like this:
<pre>
{
    "18001": "service" ,
    "8056": "admin"
 }
</pre>
Where the key is the DOCKER port (since the docker port will be consistent, whereas you might be using dynamic ports on the docker side)
The value for the port mapping will be appended to the name: 
For example, if your image is repo/foo:latest and the docker port is 18001, the name of the service in cosul would be: 
foo-service

# registering checks
You can register checks via the consul KV store.
checks should map to: http://localhost:8500/v1/kv/service/[name]/check
If your check needs to point to a specific port, you can use jinja formatting for this template.

format maps to: https://www.consul.io/docs/agent/checks.html, minus the initial 'check' root

Example:
<pre>
{
  "id": "api",
  "name": "HTTP API on port 5000",
  "http": "http://localhost:{{ checkport }}/health",
  "interval": "10s",
  "timeout": "1s"
}
</pre>

# Interactive mode
I hope to add interactive mode to this script to use it as a 'passthrough' to the actual docker command so that you can realtime register/deregister services as they are manages from docker.

# other stuff
I should probably add a cron mode as well...

