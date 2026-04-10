.PHONY: debug image2video

CHAPTER=0

debug:
	karaoke_renderer.py -a ./origin.ogg -c ${CHAPTER} --offset -200 --audio-delay 0

video.ass: lyrics.md vocal.mid
	ass_generator.py --output video.ass --offset -100 --guide-dot-duration ${GUIDE_DOT_DURATION}
	${AEGISUB_CLI} --automation kara-templater.lua video.ass video.ass 'Apply karaoke template'
	sd '( K\d.*)sans-serif' '$${1}${FONTNAME}' video.ass

output-vocal.mp4: video.mp4 origin.ogg video.ass
	ffmpeg -y -i video.mp4 -i origin.ogg -map 0:v:0 -map 1:a:0 -vf "subtitles=video.ass" -c:v libx264 -crf 20 -c:a copy output-vocal.mp4

output-visualizer.mp4: output-vocal.mp4 vocal.mid 
	midi_visualizer.py vocal.mid --offset-ms -100 --band-bg --output midi.ass
	ffmpeg -y -i output-vocal.mp4 -vf "subtitles=midi.ass" -c:a copy output-visualizer.mp4

image2video: cover.jpg
	convert cover.jpg -thumbnail 1920x1080 -background black -gravity center -extent 1920x1080 /tmp/cover-scaled.png
	ffmpeg -y -loop 1 -i /tmp/cover-scaled.png -i origin.ogg -c:v libx264 -tune stillimage -r 60 -c:a aac -b:a 320k -pix_fmt yuv420p -shortest video.mp4
