import os
import random
from PIL import Image
# Patch PIL.Image.ANTIALIAS for newer Pillow versions
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip, afx, vfx

def create_vlog(input_folder, output_path, bgm_path=None, target_resolution=(1920, 1080)):
    """
    自动剪辑生成 Vlog
    :param input_folder: 包含 mp4 视频素材的文件夹
    :param output_path: 最终输出的 mp4 文件路径
    :param bgm_path: 可选的背景音乐路径
    :param target_resolution: 目标分辨率，默认 1080p 横屏 (宽, 高)
    """
    print(f"正在扫描素材文件夹: {input_folder}")
    video_files = [f for f in os.listdir(input_folder) if f.lower().endswith(('.mp4', '.mov', '.avi'))]
    
    if not video_files:
        print("未找到任何视频素材，请检查路径。")
        return

    print(f"找到 {len(video_files)} 个视频文件。")
    
    clips = []
    # 快节奏踩点模式：每个片段截取最精华的 1.5 - 2.5 秒
    clip_duration = (1.5, 2.5) 
    
    for file in video_files:
        file_path = os.path.join(input_folder, file)
        try:
            print(f"正在处理: {file}")
            clip = VideoFileClip(file_path)
            
            # 如果原视频太短，直接跳过或者使用全部
            if clip.duration < 1.0:
                print(f"视频太短，跳过: {file}")
                clip.close()
                continue
                
            # 随机截取精华部分 (避免开头晃动)
            start_time = min(clip.duration * 0.1, 2.0) if clip.duration > 3.0 else 0
            end_time = start_time + random.uniform(*clip_duration)
            end_time = min(end_time, clip.duration)
            
            sub_clip = clip.subclip(start_time, end_time)
            
            # 处理分辨率适配 (16:9 横屏)
            # 简单粗暴的方式：按高度缩放，居中裁剪
            sub_clip = sub_clip.resize(height=target_resolution[1])
            if sub_clip.w > target_resolution[0]:
                x_center = sub_clip.w / 2
                sub_clip = sub_clip.crop(x1=x_center - target_resolution[0]/2, 
                                         y1=0, 
                                         x2=x_center + target_resolution[0]/2, 
                                         y2=target_resolution[1])
            elif sub_clip.w < target_resolution[0]:
                # 如果是竖屏视频，左右会留黑边（或者可以做高斯模糊背景，这里简化处理居中）
                sub_clip = sub_clip.on_color(size=target_resolution, color=(0,0,0), pos='center')

            # 降低原声音量 (因为是 Vlog)
            sub_clip = sub_clip.volumex(0.3)
            
            clips.append(sub_clip)
        except Exception as e:
            print(f"处理 {file} 时出错: {e}")

    if not clips:
        print("没有可用的视频片段。")
        return

    print("正在拼接视频...")
    # 可以加入淡入淡出转场，但快剪模式直接拼接更有节奏感
    final_video = concatenate_videoclips(clips, method="compose")
    
    print("视频拼接完成，正在处理音频...")
    if bgm_path and os.path.exists(bgm_path):
        try:
            bgm = AudioFileClip(bgm_path)
            # 调整 BGM 时长以匹配视频，并添加音频淡出
            bgm = bgm.set_duration(final_video.duration).audio_fadeout(2.0)
            
            # 将原声音与 BGM 混合 (如果不需要原声可以不混合)
            # 混合的话可以使用 CompositeAudioClip，这里简单起见直接替换为 BGM (更纯粹的快剪)
            final_video = final_video.set_audio(bgm)
            print("BGM 添加成功！")
        except Exception as e:
            print(f"处理 BGM 时出错: {e}")
    else:
        print("未提供 BGM 或 BGM 路径不存在，保留原声。")

    print(f"正在渲染输出到: {output_path} (这可能需要一些时间...)")
    # 使用多线程加速渲染
    final_video.write_videofile(
        output_path, 
        fps=30, 
        codec="libx264", 
        audio_codec="aac",
        threads=4,
        preset="ultrafast" # 测试时用 ultrafast，正式产出可用 medium 或 slow
    )
    
    # 释放资源
    for clip in clips:
        clip.close()
    final_video.close()
    print("🎉 Vlog 生成完毕！")

if __name__ == "__main__":
    # 配置路径
    INPUT_DIR = r"C:\Users\gaochao8\Videos\log\多劳迎"
    OUTPUT_FILE = r"vlog_output.mp4"
    # 如果你有 BGM，请取消下面这行的注释并填入路径
    # BGM_FILE = r"C:\path\to\your\bgm.mp3" 
    
    create_vlog(
        input_folder=INPUT_DIR,
        output_path=OUTPUT_FILE,
        # bgm_path=BGM_FILE,
        target_resolution=(1920, 1080) # B站/YouTube 16:9
    )
