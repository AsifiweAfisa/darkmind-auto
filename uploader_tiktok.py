import os
import requests

def upload_to_tiktok(video_path: str, caption: str) -> str:
    """
    Uploads and directly publishes a local video to TikTok using the Sandbox environment.
    Note: Sandbox videos will automatically default to Private (SELF_ONLY).
    """
    access_token = os.getenv("TIKTOK_ACCESS_TOKEN")
    
    if not access_token:
        raise ValueError("Missing TIKTOK_ACCESS_TOKEN in environment variables.")

    print("⏳ Step 1: Initializing TikTok upload...")
    file_size = os.path.getsize(video_path)
    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8"
    }
    
    payload = {
        "post_info": {
            "title": caption,
            "privacy_level": "SELF_ONLY",
            "disable_comment": False
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size, # Uploading as a single chunk for simplicity
            "total_chunk_count": 1
        }
    }
    
    init_res = requests.post(init_url, json=payload, headers=headers).json()
    
    if "error" in init_res and init_res["error"]["code"] != "ok":
        raise Exception(f"Failed to initialize TikTok upload: {init_res}")
        
    upload_url = init_res["data"]["upload_url"]
    publish_id = init_res["data"]["publish_id"]
    
    print("☁️ Step 2: Pushing video file to TikTok...")
    with open(video_path, 'rb') as f:
        video_data = f.read()
        
    upload_headers = {
        "Content-Range": f"bytes 0-{file_size-1}/{file_size}",
        "Content-Type": "video/mp4" 
    }
    
    upload_res = requests.put(upload_url, data=video_data, headers=upload_headers)
    
    if upload_res.status_code not in (200, 201, 206):
        raise Exception(f"Failed to upload video data: {upload_res.text}")
        
    print(f"✅ TikTok video published successfully! Publish ID: {publish_id}")
    return publish_id

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    # Path to any test video file in your project folder
    test_video = "videos/1cef53daa789.mp4" 
    test_caption = "Testing TikTok upload automation #DarkMind"

    if os.path.exists(test_video):
        print(f"🎬 Selected test video: {test_video}")
        upload_to_tiktok(video_path=test_video, caption=test_caption)
    else:
        print(f"❌ Error: Video file '{test_video}' not found in the current directory.")