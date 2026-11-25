#!/bin/bash
# cfbench.sh LABEL: demo's CPU ticks over a fixed Cover Flow scroll, 3 runs. Open Cover Flow first:
# the wheel sets the volume on Now Playing.
cd /Users/tcole/code/q2-pod
q() { Q2_HOST=192.168.1.45 timeout -k 5 60 python3 tools/q2remote.py "$@" < /dev/null; }
ticks() { q sh 'p=$(pidof demo); awk "{print \$14+\$15}" /proc/$p/stat; cat /proc/uptime' | tr '\n' ' '; }
for i in 1 2 3; do
  a=$(ticks); q wheel 30 --step-ms 60 >/dev/null; q wheel -30 --step-ms 60 >/dev/null; sleep 2; b=$(ticks)
  echo "$1 run$i $a -> $b" | awk '{printf "%s %s cpu_ticks=%d wall=%.2fs\n",$1,$2,$7-$3,$8-$4}'
done
