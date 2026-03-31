# Novel to Video MVP

This project automates the conversion of web novel text into short videos using AI tools.

## Prerequisites

1.  Python 3.8+
2.  `pip install -r requirements.txt`
3.  (Optional) OpenAI API Key in `.env` file for script generation.

## Usage

1.  Place your novel text in `input/novel.txt`.
2.  Run `python main.py`.
3.  Check `output/final_video.mp4`.

## Modules

-   `src/script_generator.py`: Converts text to script (Narrative + Image Prompts) using LLM.
-   `src/audio_generator.py`: Generates voiceover using Edge TTS (Free).
-   `src/image_generator.py`: Generates images using Pollinations.ai (Free).
-   `src/video_editor.py`: Assembles video using MoviePy.
