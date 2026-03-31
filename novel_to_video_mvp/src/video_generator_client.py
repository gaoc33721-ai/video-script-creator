import os
import time
import requests
import json
import asyncio

class MinimaxVideoGenerator:
    def __init__(self, api_key: str, group_id: str):
        self.api_key = api_key
        self.group_id = group_id
        # Minimax Video Generation Endpoints
        self.base_url = "https://api.minimax.chat/v1/video_generation"
        self.query_url = "https://api.minimax.chat/v1/query/video_generation" 
        
        # Model: video-01 is the standard T2V model (Hailuo)
        self.model = "video-01" 

    async def generate_video(self, prompt: str, output_path: str) -> str:
        """
        Generates a video from a text prompt using Minimax T2V.
        Returns the path to the saved video file.
        """
        print(f"🎬 [Minimax] Submitting video task: {prompt[:30]}...")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # 1. Submit Task
        payload = {
            "model": self.model,
            "prompt": prompt,
            "prompt_optimizer": True 
        }
        
        try:
            response = requests.post(self.base_url, headers=headers, json=payload)
            response.raise_for_status()
            task_data = response.json()
            
            # Check for valid task_id
            task_id = task_data.get('task_id')
            if not task_id:
                print(f"❌ Failed to submit task (No ID returned): {task_data}")
                return None
            
            # Additional check: ensure task_id is not empty string
            if str(task_id).strip() == "":
                print(f"❌ Failed to submit task (Empty ID): {task_data}")
                return None
                
            print(f"⏳ Task submitted (ID: {task_id}). Waiting for completion...")
            
            # 2. Poll for Status
            return await self._poll_status(task_id, output_path, headers)
            
        except Exception as e:
            print(f"❌ Error submitting video task: {e}")
            if 'response' in locals() and response:
                print(f"Response: {response.text}")
            return None

    async def _poll_status(self, task_id: str, output_path: str, headers: dict) -> str:
        if not task_id:
            print("❌ Cannot poll status: Task ID is missing.")
            return None

        url = f"{self.query_url}?task_id={task_id}"
        
        start_time = time.time()
        timeout = 600 # 10 minutes timeout
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(url, headers=headers)
                if response.status_code != 200:
                    print(f"⚠️ Polling failed: {response.status_code}")
                    await asyncio.sleep(5)
                    continue
                    
                result = response.json()
                status = result.get('status')
                
                if status == 'Success':
                    file_id = result.get('file_id')
                    download_url = result.get('file_url')
                    
                    if not download_url and 'base_resp' in result and result['base_resp'].get('status_msg') == 'success':
                         if file_id:
                             download_url = await self._retrieve_file_url(file_id, headers)

                    if download_url:
                        print(f"✅ Generation successful! Downloading...")
                        self._download_file(download_url, output_path)
                        return output_path
                    else:
                        print(f"❌ Success status but no URL found. Result: {result}")
                        return None
                        
                elif status == 'Fail' or status == 'Failed':
                    # Minimax failure messages can be nested
                    base_resp = result.get('base_resp', {})
                    error_msg = base_resp.get('status_msg', 'Unknown Error')
                    print(f"❌ Task failed: {status} - {error_msg}")
                    return None
                
                elif status == 'Processing' or status == 'Queueing':
                    # Still running
                    await asyncio.sleep(5)
                    
                else:
                    print(f"Status: {status}...")
                    await asyncio.sleep(5)
                    
            except Exception as e:
                print(f"Polling error: {e}")
                await asyncio.sleep(5)
                
        print("❌ Operation timed out.")
        return None

    async def _retrieve_file_url(self, file_id: str, headers: dict) -> str:
        url = f"https://api.minimax.chat/v1/files/retrieve?file_id={file_id}"
        try:
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                return data.get('file', {}).get('download_url')
        except:
            pass
        return None

    def _download_file(self, url: str, path: str):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                with open(path, 'wb') as f:
                    f.write(response.content)
                print(f"💾 Video saved to: {path}")
            else:
                print(f"❌ Failed to download video: {response.status_code}")
        except Exception as e:
             print(f"❌ Download error: {e}")

class JimengVideoGenerator:
    def __init__(self):
        self.api_key = os.getenv("VOLCENGINE_API_KEY")
        
    async def generate_video(self, prompt: str, output_path: str) -> str:
        if not self.api_key:
            print("⚠️ 缺少 VOLCENGINE_API_KEY，无法调用即梦/豆包 API。")
            print("请在 .env 中配置 VOLCENGINE_API_KEY (火山引擎)。")
            return None
        # TODO: Implement actual Volcengine call
        print("Jimeng API call not fully implemented yet.")
        return None

class SiliconFlowVideoGenerator:
    def __init__(self, api_key: str):
        self.api_key = api_key
        # SiliconFlow Video Generation Endpoint
        self.base_url = "https://api.siliconflow.cn/v1/video/submit"
        # Using Wan2.2 T2V model as requested by user's available list
        self.model = "Wan-AI/Wan2.2-T2V-A14B"

    async def generate_video(self, prompt: str, output_path: str) -> str:
        print(f"🎬 [SiliconFlow] Submitting video task: {prompt[:30]}...")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "prompt": prompt,
        }
        
        try:
            response = requests.post(self.base_url, headers=headers, json=payload)
            
            if response.status_code != 200:
                print(f"❌ SiliconFlow Submit Failed: {response.text}")
                return None
                
            data = response.json()
            request_id = data.get('requestId') 
            
            if not request_id:
                 print(f"❌ No Request ID from SiliconFlow: {data}")
                 return None

            print(f"⏳ Task submitted (ID: {request_id}). Waiting...")
            return await self._poll_status(request_id, output_path, headers)

        except Exception as e:
            print(f"❌ SiliconFlow Error: {e}")
            return None

    async def _poll_status(self, request_id: str, output_path: str, headers: dict) -> str:
        # GET https://api.siliconflow.cn/v1/video/status
        url = "https://api.siliconflow.cn/v1/video/status"
        
        for _ in range(60): # 5 minutes timeout
            try:
                res = requests.post(url, headers=headers, json={"requestId": request_id})

                if res.status_code == 200:
                    data = res.json()
                    status = data.get('status') # processing, success, fail
                    
                    if status == 'Succeed':
                        results = data.get('results', [])
                        if results:
                            # Usually returns a list of result objects
                            video_url = results[0].get('url')
                            if video_url:
                                self._download_file(video_url, output_path)
                                return output_path
                    elif status == 'Failed':
                        print(f"❌ Task Failed: {data}")
                        return None
                
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Polling error: {e}")
                await asyncio.sleep(5)
        return None

    def _download_file(self, url: str, path: str):
        r = requests.get(url)
        with open(path, 'wb') as f:
            f.write(r.content)
