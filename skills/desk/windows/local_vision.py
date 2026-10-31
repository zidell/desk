"""Offline CUDA/CPU vision backend. Answers are evidence, never autonomous actions."""
from pathlib import Path
import hashlib


def load_vision(model_id):
    import torch
    from transformers import AutoProcessor, AutoModelForImageTextToText
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    processor = AutoProcessor.from_pretrained(model_id, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(model_id, local_files_only=True,
                                                       torch_dtype=dtype, attn_implementation="sdpa").to(device).eval()

    @torch.inference_mode()
    def answer(image, question, tokens=80):
        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": question}]}]
        prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = processor(text=prompt, images=[image], return_tensors="pt").to(device, dtype)
        output = model.generate(**inputs, max_new_tokens=tokens, do_sample=False)
        return processor.batch_decode(output[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()

    return answer


def load_captioner(model_id):
    from PIL import Image
    answer = load_vision(model_id)
    cache = {}

    def caption(path, ox, oy, pw, boxes):
        results = []
        with Image.open(path) as source:
            im = source.convert("RGB")
        scale = im.width/pw
        # Captions only label unnamed controls; bound the number of model calls per screen.
        for x,y,w,h in boxes[:24]:
            crop = im.crop((int((x-ox)*scale),int((y-oy)*scale),int((x+w-ox)*scale),int((y+h-oy)*scale)))
            crop.thumbnail((192,192))
            digest=hashlib.sha256(crop.tobytes()).hexdigest()
            if digest not in cache:
                cache[digest]=answer(crop,"Describe this UI icon in a few words. If unclear, say unclear.",20).replace("\n"," ")
                if len(cache)>2000:
                    cache.pop(next(iter(cache)))
            results.append(cache[digest])
        return results+[""]*max(0,len(boxes)-len(results))
    return caption
