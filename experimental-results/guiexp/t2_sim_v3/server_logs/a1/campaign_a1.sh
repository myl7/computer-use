#!/bin/bash
# W7b a1 campaign: DS phase then GLM phase, 24 shards x 4 workers each
# (same geometry as W4).  Logs to logs_a1/campaign.log.
set -u
BASE=$HOME/app/guiexp/sim_w4
echo "campaign start $(date '+%F_%T')" >> $BASE/logs_a1/campaign.log
$BASE/w4b_run_shards.sh $BASE/w4b_shards_ds_a1.txt  android_ds  10.509389227369171 24 4
$BASE/w4b_run_shards.sh $BASE/w4b_shards_glm_a1.txt android_glm 1.0                24 4
echo "campaign end $(date '+%F_%T')" >> $BASE/logs_a1/campaign.log
