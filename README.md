# Scripts to make karaoke subtitle in terminal

## Example
See `./example/` for a project example, files in step1 and step2 are already prepared, you can skip these two steps if you just want to see the final effects.

### 0. make sure the scripts are executable & dependency installation
```shell
uv sync
chmod +x *.py
```

### 1. `lyrics.md` syntax

`---`: Used to split chapters.

`青[あお]い`: `[]` is used for hiragana notation  

`、.0`: `\.\d+` is used to override the total mora of a character 

`(幾千)[いくせん].1` they can be used together

### 2. make `vocal.mid`
It is recommended to use [wavetone](https://ackiesound.ifdef.jp/download.html) to make a midi file of the music.

### 3. edit `.envrc`

```shell
export OVERLAY_COLOR=6EB7E3  # primary color for subtitle
export BPM=160  # audio bpm
# path to https://github.com/Myaamori/aegisub-cli executable
export AEGISUB_CLI="$HOME/repo/aegisub-cli/builddir/src/aegisub-cli"
export FONTNAME="FOT-Seurat Pro DB" # subtitle font make sure you have it installed
```

For others environment variables in `.envrc`, there is no need to override. 

If you have `direnv` installed, just run `direnv allow`. You can also maunally load them by `source .envrc`.

### 4. make video from cover.jpg
Run `make image2video`.

### 5. make karaoke subtitle and the final video

Run `make output-vocal.mp4`

### 6. (optional) debug in terminal

Run `make debug CHAPTER=<CHAPTER_INDEX>`.
