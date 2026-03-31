try:
    from moviepy.editor import ImageClip
    print("Import from moviepy.editor successful")
except ImportError:
    print("Import from moviepy.editor failed")
    try:
        from moviepy import ImageClip
        print("Import from moviepy successful")
    except ImportError:
        print("Import from moviepy failed completely")
