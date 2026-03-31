import moviepy
print(dir(moviepy))
try:
    from moviepy import ImageClip
    print("ImageClip found")
except ImportError:
    print("ImageClip NOT found in top level")
