import os
import time

def generate_upload_guide(video_path, platform="bilibili"):
    """
    生成对应平台的发布指南
    """
    print("="*50)
    print(f"🎉 您的 Vlog 已成功生成！")
    print(f"📁 视频保存路径: {os.path.abspath(video_path)}")
    print("="*50)
    
    if platform.lower() in ["bilibili", "b站", "youtube"]:
        print("\n【B站 / YouTube 横屏上传指南】")
        print("1. 打开浏览器并登录您的账号 (Bilibili创作中心 或 YouTube Studio)。")
        print("2. 点击右上角的【投稿】或【创建】按钮。")
        print(f"3. 将刚刚生成的视频文件拖拽到上传区域：\n   -> {os.path.abspath(video_path)}")
        print("4. 填写视频信息：")
        print("   - 标题：建议带上【Vlog】、【日常】等关键词，例如：“Action4 记录的周末碎片 | 我的治愈日常”")
        print("   - 标签：#Vlog #生活记录 #Action4 #运动相机")
        print("   - 简介：简单描述一下拍摄背景或心情。")
        print("5. (重要) 选择【存为草稿】或【定时发布】，以便您在手机端或稍后预览效果后再正式发布。")
        
    elif platform.lower() in ["douyin", "抖音", "xiaohongshu", "小红书"]:
        print("\n【抖音 / 小红书 竖屏上传指南】")
        print("1. 建议将生成的视频文件传输到手机上（通过微信文件传输助手、隔空投送等）。")
        print("2. 打开 抖音 或 小红书 APP。")
        print("3. 点击底部中间的【+】号，选择相册中刚刚传输的视频。")
        print("4. 您可以在 APP 内进一步添加平台热门音乐、滤镜或文字贴纸。")
        print("5. 填写文案并带上热门话题，例如：#我的日常 #vlog日常 #碎片生活")
        print("6. 点击【存草稿】，您可以在个人主页的草稿箱中找到它，随时准备发布！")
    
    print("\n💡 进阶提示：如果需要全自动化上传，可以了解 Playwright 或 Selenium 自动化测试工具，或申请各平台的开发者 API 权限。")
    print("="*50)

if __name__ == "__main__":
    generate_upload_guide("vlog_output.mp4", "bilibili")