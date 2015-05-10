# dockingstation
A service to add  docker containers to consul for service discovery

# Options

-h/--help         -  print usage summary
-i/--interactive  -  interactive mode: passthrough to docker CLI (not implemented yet)
-d/--daemon       -  daemon mode: poll docker for new services and register them
-e/--env          -  environment: this will add a tag inside consul for your environment
--debug           -  enable debug output

# General functionality
This script will poll 'docker ps' for entries and add them to consul via API.

# daemon mode
This is currently the only available option. You can run this under something like supervisor as a 'background' process.

# registering checks
You can register checks via the consul KV store.
checks should map to: http://localhost:8500/v1/kv/checks/[name]
format maps to: https://www.consul.io/docs/agent/checks.html, minus the initial 'check' root
Example:
<pre>
{
  "id": "api",
  "name": "HTTP API on port 5000",
  "http": "http://localhost:5000/health",
  "interval": "10s",
  "timeout": "1s"
}
</pre>

# Interactive mode
I hope to add interactive mode to this script to use it as a 'passthrough' to the actual docker command so that you can realtime register/deregister services as they are manages from docker.

# other stuff
I should probably add a cron mode as well...

