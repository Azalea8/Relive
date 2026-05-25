# FFmpeg 极简编译配置 (Windows 64 位)

ReLive 专用 — 录制/HLS 切片/拼接/ASS 烧录/硬编导出。

## 设计原则

- 硬件编码器覆盖 NVENC / AMF（95%+ Windows 用户）
- CPU 软编 `libx264` 编码器兜底


## 配置

```bash
./configure \
    --prefix=./ff_build \
    --disable-everything \
    --enable-ffmpeg \
    --enable-ffprobe \
    --disable-ffplay \
    --disable-doc \
    --disable-debug \
    --enable-small \
    --enable-gpl \
    --enable-version3 \
    --enable-libx264 \
    --enable-libass \
    --enable-openssl \
    --enable-nvenc \
    --enable-amf \
    --enable-d3d11va \
    --enable-dxva2 \
    --enable-hwaccel=h264_d3d11va,h264_dxva2,hevc_d3d11va,hevc_dxva2 \
    --enable-demuxer=flv,hls,mpegts,mov,matroska,concat,ass,subrip,webvtt \
    --enable-muxer=flv,hls,mpegts,mp4,matroska,segment,null \
    --enable-protocol=file,concat,hls,http,https,tls,,pipe,crypto \
    --enable-bsf=h264_mp4toannexb,aac_adtstoasc,hevc_mp4toannexb,extract_extradata \
    --enable-parser=h264,hevc,aac,mpegaudio \
    --enable-decoder=h264,hevc,aac,mp3float,ssa,subrip,webvtt,mov_text \
    --enable-encoder=libx264,aac,h264_nvenc,h264_amf \
    --enable-filter=ass,subtitles,scale,fps,format,trim,setpts,aformat,amix,null,anull,buffer,buffersink \
    --enable-indev=lavfi \
    --pkg-config-flags="--static" \
    --extra-cflags="-I/mingw64/include" \
    --extra-ldflags="-L/mingw64/lib"
```

## 编译

```bash
make -j$(nproc)
make install
```

## 功能清单

| 类别 | 支持项 |
|------|--------|
| 解码 | H264, HEVC, AAC, MP3 |
| 编码 | h264, aac, NVENC, AMF (全部原生，无外部库) |
| 容器 | FLV, HLS, MPEG-TS, MP4, Matroska, Concat |
| 网络 | HTTP, HTTPS, TLS |
| 字幕 | ASS 烧录 (libass) |
| 硬解 | D3D11VA, DXVA2 |
| 硬编 | NVIDIA NVENC, AMD AMF |
| 滤镜 | ass, scale, fps, format, trim, setpts, aformat, amix |

## 体积

ffmpeg.exe ffprobe.exe 合计10MB

多个dll 合计20MB
