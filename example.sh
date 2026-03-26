#!/bin/bash

# example usecase for midi_visualizer
python midi_visualizer.py vocal.mid \
        --output midiv.mp4 \
        --timeline-ratio 0.32 \
        --waterfall-coverage 0.4 \
        --lane-overlap 0.28 \
        --note-gap 0.035 \
        --width 1920 --height 1080 \
        --fps 60 --workers 8 --vaapi

ffmpeg -y -i video.mp4 -i midiv.mp4 \
                -filter_complex "[1:v]format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lt(Y,H*0.4),if(lte(abs(r(X,Y) - 9), 20) * lte(abs(g(X,Y) - 9), 20) * lte(abs(b(X,Y) - 9), 20), 255 * 0.20, 255 * 0.55),0)'[viz];[0:v][viz]overlay=0:0:format=auto[v]" \
      -map "[v]" -map 0:a? -c:v libx264 -crf 20 -preset medium -c:a copy output_overlay2.mp4
