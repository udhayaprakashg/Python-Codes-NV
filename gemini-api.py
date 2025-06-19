
api_key = 'AIzaSyDuqNgYtxylYCfRjV-w1lb12YLE8AjeCL0'

from google import genai
client = genai.Client(api_key=api_key)
model = "gemini-2.0-flash"
# 1. General Response
'''
response = client.models.generate_content(
    model = model,
    contents = "Why is the sky is the blue"

)
#print(response.text)
'''

# 2. Conversation
'''
chat = client.chats.create(model=model)

while True:
    message= input('> ')
    if message == 'exit':
        break
     
    res = chat.send_message(message)
    print(res.text)
'''

# 3. Upload Image
uploaded_file = client.files.upload(file =r"C:\Users\Udhaya\Downloads\Screenshot_2025-06-10-14-52-06-522_com.whatsapp.jpg" )
response = client.models.generate_content(
    model = model,
    contents = ["Descript this image", uploaded_file]

)
print(response.text)