# Only enable the remote controller when the review UI has a private password.
# Port 11334 stays inside the Docker Compose network.
{% if env.CONTROLLER_PASSWORD %}
bind_socket = "0.0.0.0:11334";
secure_ip = ["127.0.0.1", "::1"];
enable_password = "{= env.CONTROLLER_PASSWORD | pbkdf =}";
{% else %}
bind_socket = "127.0.0.1:11334";
{% endif %}
