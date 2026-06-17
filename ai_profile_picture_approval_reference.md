# AI Profile Picture Approval Reference

This document contains the prompt template and instructions used by the AI-powered profile picture approval system (`profile_picture_approval`). You can use this content to guide another AI in coding a similar photo approval/denial system.

---

## 1. System Prompt (`AI_PROMPT`)

The following prompt is sent to the vision model (e.g., `gpt-4o-mini`) along with the image URL to evaluate the photo:

```text
Evaluate this profile picture. Approve if ALL criteria are met:
- Face forward (deny if turned 45°+ sideways or obscured)
- Face close to camera and in focus
- No sunglasses or masks (hats OK if eyes visible; prescription glasses are ALWAYS OK if eyes are visible through lenses)
- No heavy beauty filters or AR distortions
- No nudity, hate symbols, offensive gestures, or weapons

Respond with JSON only:
{"suitable": true|false, "reason": "polite explanation if false", "confidence": 0.0-1.0}
```

---

## 2. API Usage Structure

When sending the image to the model for evaluation, the prompt is provided as text alongside the image URL in the same message. 

Here is the JSON structure sent to the OpenAI Chat Completions API:

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text", 
          "text": "Evaluate this profile picture. Approve if ALL criteria are met:\n- Face forward (deny if turned 45°+ sideways or obscured)\n- Face close to camera and in focus\n- No sunglasses or masks (hats OK if eyes visible; prescription glasses are ALWAYS OK if eyes are visible through lenses)\n- No heavy beauty filters or AR distortions\n- No nudity, hate symbols, offensive gestures, or weapons\n\nRespond with JSON only:\n{\"suitable\": true|false, \"reason\": \"polite explanation if false\", \"confidence\": 0.0-1.0}"
        },
        {
          "type": "image_url", 
          "image_url": {
            "url": "https://example.com/path/to/photo.jpg", 
            "detail": "low"
          }
        }
      ]
    }
  ],
  "response_format": {"type": "json_object"},
  "temperature": 0.2
}
```

## 3. Expected Output

The model is expected to respond strictly with a JSON object, adhering to the requested schema. 

**Example Approved Response:**
```json
{
  "suitable": true,
  "reason": "",
  "confidence": 0.98
}
```

**Example Denied Response:**
```json
{
  "suitable": false,
  "reason": "The face is obscured by dark sunglasses.",
  "confidence": 0.95
}
```
