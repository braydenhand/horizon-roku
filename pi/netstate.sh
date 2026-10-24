#!/bin/sh
# Print the machine's networking state in a stable order, so you can diff it
# before and after changing anything on a Pi that also runs other network
# services (DNS filtering, VPNs, ...). Horizon's watcher should never change it.
#
#   sh netstate.sh > before.txt   ...make changes...   sh netstate.sh > after.txt
#   diff before.txt after.txt
#
# Pass service names to also record whether they're running:
#   sh netstate.sh roku-watcher ssh
echo "### ip rule";          ip rule show
echo "### routes (all tables)"; ip route show table all | grep -v "^local\|^broadcast" | sort
echo "### addresses";        ip -brief addr
echo "### iptables";         sudo -n iptables-save 2>/dev/null | grep -vE "^#|^:" | sort
echo "### nft ruleset";      sudo -n nft list ruleset 2>/dev/null | sed -E 's/(packets|bytes) [0-9]+/\1 N/g'
echo "### ip_forward";       cat /proc/sys/net/ipv4/ip_forward
echo "### resolv.conf";      cat /etc/resolv.conf
echo "### listening tcp";    ss -ltnH | awk '{print $4}' | sort -u
echo "### listening udp";    ss -lunH | awk '{print $4}' | sort -u
if [ $# -gt 0 ]; then
  echo "### services";       for s in "$@"; do echo "$s: $(systemctl is-active "$s")"; done
fi
