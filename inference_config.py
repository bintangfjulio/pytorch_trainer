import torch
import emoji
import re
import torch.nn as nn
import torch.nn.functional as F

from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory
from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
from transformers import BertTokenizer, BertModel


class BERT_CNN(nn.Module):
    def __init__(self, window_sizes):
        super(BERT_CNN, self).__init__()
        self.pretrained_bert = BertModel.from_pretrained("indolem/indobert-base-uncased", output_attentions=False, output_hidden_states=True)
        
        conv_layers = []
        for window_size in window_sizes:
            conv_layer = nn.Conv2d(4, 32, (window_size, self.pretrained_bert.embeddings.word_embeddings.weight.size(1)))
            conv_layers.append(conv_layer)
            
        self.cnn = nn.ModuleList(conv_layers)

        self.dropout = nn.Dropout(0.1) 
        self.window_length = len(window_sizes)
        self.num_bert_states = 4

    def forward(self, input_ids, attention_mask):
        bert_output = self.pretrained_bert(input_ids=input_ids, attention_mask=attention_mask)
        stacked_hidden_states = torch.stack(bert_output.hidden_states[-self.num_bert_states:], dim=1)

        pooling = []
        for layer in self.cnn:
            hidden_states = layer(stacked_hidden_states)
            relu_output = F.relu(hidden_states.squeeze(3))
            pooling.append(relu_output)

        max_pooling = []
        for features in pooling:
            pooled_features = F.max_pool1d(features, features.size(2)).squeeze(2)
            max_pooling.append(pooled_features)
        
        concatenated = torch.cat(max_pooling, dim=1)
        preds = self.dropout(concatenated)
        
        return preds
    

class Inference():
    def __init__(self):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
        self.labels = ["Teknik Informatika", "Teknik Multimedia Digital", "Teknik Multimedia dan Jaringan"]
        self.kbk_temp = ["Sistem Cerdas", "Multimedia & Teknologi: AI Game", "Jaringan & IoT"]

        self.stop_words = StopWordRemoverFactory().get_stop_words()
        self.tokenizer = BertTokenizer.from_pretrained("indolem/indobert-base-uncased", use_fast=False)
        self.stemmer = StemmerFactory().create_stemmer()
        self.max_length = 360
        
        self.window_sizes = [1, 2, 3, 4, 5]
        self.model = BERT_CNN(window_sizes=self.window_sizes)
        self.output_layer = nn.Linear(len(self.window_sizes) * 32, len(self.labels))

        checkpoint = torch.load('checkpoint/flat_prodi_model.pt', map_location=self.device)
        self.model.load_state_dict(checkpoint["hidden_states"])
        self.output_layer.load_state_dict(checkpoint["last_hidden_state"])
        
        self.model.to(self.device)
        self.output_layer.to(self.device)

    def text_processing(self, abstrak, kata_kunci):
        text = str(kata_kunci) + " - " + str(abstrak)
        text = text.lower()
        text = emoji.replace_emoji(text, replace='') 
        text = re.sub(r'\n', ' ', text) 
        text = re.sub(r'http\S+', '', text)  
        text = re.sub(r'\d+', '', text)  
        text = re.sub(r'[^a-zA-Z ]', '', text)  
        text = ' '.join([word for word in text.split() if word not in self.stop_words])  
        text = self.stemmer.stem(text)
        text = text.strip()      
        token = self.tokenizer.encode_plus(
                    text=text,
                    add_special_tokens=True,
                    max_length=self.max_length,
                    return_tensors='pt',
                    padding="max_length", 
                    truncation=True
                )

        return token['input_ids'], token['attention_mask']

    def classification(self, abstrak, kata_kunci):
        input_ids, attention_mask = self.text_processing(abstrak, kata_kunci)

        self.model.eval()
        with torch.no_grad():
            preds = self.model(input_ids=input_ids.to(self.device), attention_mask=attention_mask.to(self.device))
            preds = self.output_layer(preds)
            result = torch.softmax(preds, dim=1)[0]

            probs = {}
            for index, prob in enumerate(result):
                probs[self.labels[index]] = round(prob.item() * 100, 2)

            highest_prob = torch.argmax(preds, dim=1)

        return probs, self.kbk_temp[highest_prob]