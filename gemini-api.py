
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


#--------------- Torch

from transformers import AutoModel, AutoTokenizer
import torch

# Load Bio_ClinicalBERT
model_name = "emilyalsentzer/Bio_ClinicalBERT"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModel.from_pretrained(model_name)

paragraph = "agonist is a good medicine is called paracetamol but it is diluted easily in solution"
search_terms = ["agonist", "anti aging", "medicine"]  # List of search terms

# Split paragraph into words (remove punctuation)
paragraph_words = [word.strip(".,") for word in paragraph.split()]

# Encode all paragraph words once (for efficiency)
paragraph_embeddings = []
for p_word in paragraph_words:
    p_inputs = tokenizer(p_word, return_tensors="pt", truncation=True)
    with torch.no_grad():
        p_outputs = model(**p_inputs)
    p_embedding = p_outputs.last_hidden_state.mean(dim=1)  # [1, 768]
    paragraph_embeddings.append(p_embedding)

# Process each search term
results = {}
for term in search_terms:
    # Encode the search term
    s_inputs = tokenizer(term, return_tensors="pt", truncation=True)
    with torch.no_grad():
        s_outputs = model(**s_inputs)
    s_embedding = s_outputs.last_hidden_state.mean(dim=1)  # [1, 768]
    
    # Compare against all paragraph words
    best_match = None
    highest_similarity = -1
    for p_word, p_embedding in zip(paragraph_words, paragraph_embeddings):
        similarity = torch.cosine_similarity(s_embedding, p_embedding).item()
        if similarity > highest_similarity:
            highest_similarity = similarity
            best_match = p_word
    
    results[term] = {
        "best_match": best_match,
        "similarity": highest_similarity
    }

# Print results
print(f"Paragraph: '{paragraph}'\n")
for term, data in results.items():
    print(f"Search Term: '{term}'")
    print(f"  Best Match: '{data['best_match']}' (Similarity: {data['similarity']:.2f})")
    print("-" * 40)
