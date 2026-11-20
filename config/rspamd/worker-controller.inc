# Configure the private controller for learning requests.
# When configured, the secret is loaded from RSPAMD_CONTROLLER_PASSWORD.
# Port 11334 stays inside the Docker Compose network.
{% if env.CONTROLLER_PASSWORD %}
bind_socket = "0.0.0.0:11334";
secure_ip = ["127.0.0.1", "::1"];
enable_password = "{= env.CONTROLLER_PASSWORD | pbkdf =}";
allow_file_and_shm_inputs = false;
{% else %}
bind_socket = "127.0.0.1:11334";
{% endif %}
