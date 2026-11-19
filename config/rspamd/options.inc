dns {
  # Rspamd 4.x accepts resolver hostnames. Keep RBL/DKIM/SPF lookups off
  # Docker's forwarding resolver and on SpamHop's private recursive resolver.
  nameserver = ["unbound"];
  timeout = 2s;
  retransmits = 2;
  sockets = 16;
}
