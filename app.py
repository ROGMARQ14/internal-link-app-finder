import streamlit as st
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
import re
import sys
import os
import importlib
import spacy
import torch
from sentence_transformers import SentenceTransformer
from transformers import pipeline
import spacy_streamlit

# Download required NLTK data
@st.cache_resource
def download_nltk_data():
    nltk.download('punkt')
    nltk.download('stopwords')
    nltk.download('wordnet')

# Load models
@st.cache_resource
def load_models():
    # Show a spinner while loading models
    with st.spinner("Loading NLP models... (this may take a moment)"):
        # Check for CUDA availability
        cuda_available = torch.cuda.is_available()
        if cuda_available:
            st.success("✅ CUDA is available! Using GPU for faster processing")
        
        # Load spaCy model
        try:
            nlp = spacy.load("en_core_web_sm")
            st.success("✅ Successfully loaded spaCy model")
        except OSError as e:
            st.warning(f"Could not load spaCy model: {e}")
            st.info("Downloading spaCy model. This may take a moment...")
            
            # Use spacy_streamlit to download model
            spacy_streamlit.download_model("en_core_web_sm")
            try:
                nlp = spacy.load("en_core_web_sm")
                st.success("✅ Successfully downloaded and loaded spaCy model")
            except Exception as e:
                st.error(f"Error loading spaCy model after download: {e}")
                return None, None, None
        
        # Load sentence-transformers model
        try:
            device = 'cuda' if cuda_available else 'cpu'
            model = SentenceTransformer('all-MiniLM-L6-v2', device=device)
            st.success(f"✅ Successfully loaded sentence transformer model on {device}")
        except Exception as e:
            st.error(f"Error loading sentence transformer model: {e}")
            return nlp, None, None
        
        # Load NER model
        try:
            device = 0 if cuda_available else -1  # 0=cuda, -1=cpu
            keyword_extractor = pipeline("ner", model="dbmdz/bert-large-cased-finetuned-conll03-english", device=device)
            st.success(f"✅ Successfully loaded NER model on {'GPU' if device==0 else 'CPU'}")
        except Exception as e:
            st.error(f"Error loading NER model: {e}")
            return nlp, model, None
        
        return nlp, model, keyword_extractor

def preprocess_text(text):
    """Clean and preprocess text."""
    # Convert to lowercase
    text = text.lower()
    # Remove special characters and numbers
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\d+', ' ', text)
    # Remove extra whitespaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_keywords(text, nlp, keyword_extractor, top_n=20):
    """Extract keywords from text using NER and keyword extraction."""
    # Initialize keywords list
    all_keywords = []
    
    # Extract named entities with spaCy if available
    if hasattr(nlp, 'ents'):
        try:
            # Process text with spaCy
            doc = nlp(text)
            
            # Extract named entities
            entities = [ent.text.lower() for ent in doc.ents if len(ent.text) > 2]
            all_keywords.extend(entities)
            
            # Extract noun phrases if available
            if hasattr(doc, 'noun_chunks'):
                noun_phrases = [chunk.text.lower() for chunk in doc.noun_chunks if len(chunk.text) > 2]
                all_keywords.extend(noun_phrases)
        except Exception as e:
            st.warning(f"Error in spaCy keyword extraction: {e}")
    
    # Use BERT-based keyword extraction if available
    try:
        # Process text in chunks to avoid token limit issues
        chunks = [text[i:i+512] for i in range(0, len(text), 512)]
        for chunk in chunks:
            results = keyword_extractor(chunk)
            for result in results:
                if len(result['word']) > 2:
                    all_keywords.append(result['word'].lower())
    except Exception as e:
        st.warning(f"BERT keyword extraction error: {e}")
    
    # Fall back to simple TF-IDF extraction if needed
    if not all_keywords:
        try:
            # Use TF-IDF to extract important words
            vectorizer = TfidfVectorizer(max_features=top_n*2, stop_words='english')
            tfidf_matrix = vectorizer.fit_transform([text])
            feature_names = vectorizer.get_feature_names_out()
            
            # Sort by TF-IDF score
            scores = zip(feature_names, tfidf_matrix.toarray()[0])
            sorted_scores = sorted(scores, key=lambda x: x[1], reverse=True)
            
            # Add top keywords
            all_keywords.extend([word for word, score in sorted_scores[:top_n*2]])
        except Exception as e:
            st.warning(f"TF-IDF keyword extraction error: {e}")
            
            # Last resort: just take words longer than 3 characters
            words = re.findall(r'\b\w{4,}\b', text.lower())
            all_keywords.extend(words[:top_n*3])
    
    # Remove duplicates and sort by frequency
    keyword_freq = {}
    for kw in all_keywords:
        kw = kw.strip()
        if kw and len(kw) > 2:
            keyword_freq[kw] = keyword_freq.get(kw, 0) + 1
    
    # Sort by frequency and return top N
    sorted_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)
    return [k for k, v in sorted_keywords[:top_n]]

def generate_semantic_variations(keywords, model, nlp):
    """Generate semantically related variations of keywords."""
    variations = []
    
    # Start with the original keywords
    variations.extend(keywords)
    
    # Add simple variations if spaCy not fully functional
    if not hasattr(next(iter([]), 'similarity') if hasattr(nlp, 'vocab') and nlp.vocab else [], 'similarity'):
        # Simple word variations (singular/plural, etc.)
        for keyword in keywords:
            # Add basic variations
            if keyword.endswith('s'):
                variations.append(keyword[:-1])  # Remove 's'
            else:
                variations.append(keyword + 's')  # Add 's'
                
            # Add variations with common prefixes/suffixes
            variations.append(keyword + 'ing')
            variations.append(keyword + 'ed')
            
            # Add hyphenated and non-hyphenated versions
            if '-' in keyword:
                variations.append(keyword.replace('-', ' '))
            elif ' ' in keyword:
                variations.append(keyword.replace(' ', '-'))
    else:
        # Use spaCy for more advanced variations if available
        for keyword in keywords:
            # Get embeddings for the keyword
            doc = nlp(keyword)
            
            # Find similar words using spaCy
            for token in doc:
                if hasattr(token, 'vector_norm') and token.vector_norm:
                    for similar_word in token.vocab:
                        if similar_word.is_lower and similar_word.prob >= -15 and similar_word.has_vector:
                            similarity = token.similarity(similar_word)
                            if similarity > 0.7 and similar_word.text != token.text:
                                variations.append(similar_word.text)
    
    # Remove duplicates
    variations = list(set(variations))
    return variations

def extract_content_snippets(content, keyword, window_size=150):
    """Extract content snippets around keyword occurrences."""
    snippets = []
    content_lower = content.lower()
    keyword_lower = keyword.lower()
    
    # Find all occurrences of the keyword
    start_positions = [m.start() for m in re.finditer(r'\b' + re.escape(keyword_lower) + r'\b', content_lower)]
    
    for start in start_positions:
        # Determine snippet boundaries
        snippet_start = max(0, start - window_size)
        snippet_end = min(len(content), start + len(keyword) + window_size)
        
        # Extract snippet
        snippet = content[snippet_start:snippet_end]
        
        # Add ellipsis if needed
        if snippet_start > 0:
            snippet = "..." + snippet
        if snippet_end < len(content):
            snippet = snippet + "..."
        
        # Highlight the keyword in the snippet (preserving original case)
        original_case_keyword = content[start:start+len(keyword)]
        snippet = snippet.replace(original_case_keyword, f"**{original_case_keyword}**")
        
        snippets.append(snippet)
    
    return snippets

def check_existing_anchor(content, keyword, destination_url):
    """Check if there's already an anchor with the given keyword linking to the destination."""
    # Simple regex pattern to find links containing the keyword
    pattern = rf'<a\s+[^>]*href=["\']([^"\']*)["\'][^>]*>{keyword}</a>'
    matches = re.finditer(pattern, content, re.IGNORECASE)
    
    for match in matches:
        href = match.group(1)
        if destination_url in href:
            return True
    
    return False

def suggest_new_content(original_content, keyword, window_size=200):
    """Suggest new content or modifications to include the keyword."""
    # Find potential insertion points in the content
    sentences = nltk.sent_tokenize(original_content)
    best_sentence_idx = -1
    best_similarity = -1
    
    # Create a simple representation of the keyword
    keyword_tokens = set(nltk.word_tokenize(keyword.lower()))
    
    # Find the most relevant sentence for insertion
    for i, sentence in enumerate(sentences):
        sentence_tokens = set(nltk.word_tokenize(sentence.lower()))
        # Calculate Jaccard similarity
        if len(keyword_tokens) > 0 and len(sentence_tokens) > 0:
            similarity = len(keyword_tokens.intersection(sentence_tokens)) / len(keyword_tokens.union(sentence_tokens))
            if similarity > best_similarity:
                best_similarity = similarity
                best_sentence_idx = i
    
    if best_sentence_idx >= 0:
        # Generate a suggestion by modifying the best sentence
        original_sentence = sentences[best_sentence_idx]
        
        # Simple suggestion: Add the keyword if it's not already there
        if keyword.lower() not in original_sentence.lower():
            # Find a good insertion point (after a comma or period)
            insertion_points = [m.start() for m in re.finditer(r'[,.]', original_sentence)]
            if insertion_points:
                insertion_point = insertion_points[-1] + 1
                new_sentence = original_sentence[:insertion_point] + f" {keyword} is also relevant here." + original_sentence[insertion_point:]
            else:
                # If no good insertion point, append to the end
                new_sentence = original_sentence + f" {keyword} is also relevant in this context."
            
            # Show original and suggested modification
            return f"Original: {original_sentence}\nSuggested: {new_sentence}"
        else:
            # If keyword already exists but not as an anchor
            return f"The keyword '{keyword}' already exists in the content but is not linked. Consider converting it to an anchor text."
    
    # If no good match found
    return f"Consider adding a new paragraph or sentence mentioning '{keyword}' in a relevant context."

def calculate_similarity_score(text1, text2, model):
    """Calculate semantic similarity between two texts using sentence transformers."""
    try:
        # Encode texts to get embeddings
        embedding1 = model.encode(text1, convert_to_tensor=True)
        embedding2 = model.encode(text2, convert_to_tensor=True)
        
        # Calculate cosine similarity
        cos_sim = torch.nn.functional.cosine_similarity(embedding1.unsqueeze(0), embedding2.unsqueeze(0))
        similarity_score = float(cos_sim) * 100
        
        return round(similarity_score, 2)
    except Exception as e:
        st.warning(f"Error calculating similarity: {e}, using fallback method")
        # Fallback to sklearn's cosine similarity
        from sklearn.metrics.pairwise import cosine_similarity
        
        # Get embeddings as numpy arrays
        emb1 = model.encode(text1, convert_to_tensor=False)
        emb2 = model.encode(text2, convert_to_tensor=False)
        
        # Reshape for cosine_similarity
        e1 = emb1.reshape(1, -1)
        e2 = emb2.reshape(1, -1)
        
        similarity_score = float(cosine_similarity(e1, e2)[0][0]) * 100
        return round(similarity_score, 2)

def main():
    st.set_page_config(page_title="Context-Aware Internal Link Finder", page_icon="🔗", layout="wide")
    
    st.title("Context-Aware Automatic Keyword Interlinker")
    st.markdown("""
    This app helps you find contextually relevant internal linking opportunities within your content.
    Upload your Google Search Console data and content file to get started.
    """)
    
    # Download required data
    download_nltk_data()
    
    # Load models
    nlp, semantic_model, keyword_extractor = load_models()
    
    # File uploaders
    st.header("Upload Your Data")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Google Search Console Data")
        gsc_file = st.file_uploader("Upload GSC Performance Report (CSV or XLSX)", type=["csv", "xlsx"])
        
    with col2:
        st.subheader("Content Data")
        content_file = st.file_uploader("Upload Content File (CSV or XLSX)", type=["csv", "xlsx"])
    
    # Parameters
    st.header("Configuration")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        top_queries = st.number_input("Number of top queries per page", min_value=5, max_value=50, value=10)
    
    with col2:
        similarity_threshold = st.slider("Similarity Threshold (%)", min_value=50, max_value=95, value=70)
    
    with col3:
        max_suggestions = st.number_input("Maximum suggestions per page", min_value=1, max_value=50, value=5)
    
    # Process data
    if st.button("Find Internal Linking Opportunities"):
        if gsc_file is None or content_file is None:
            st.error("Please upload both required files.")
        else:
            with st.spinner("Processing data and finding opportunities..."):
                # Load GSC data
                if gsc_file.name.endswith('.csv'):
                    gsc_data = pd.read_csv(gsc_file)
                else:
                    gsc_data = pd.read_excel(gsc_file)
                
                # Load content data
                if content_file.name.endswith('.csv'):
                    content_data = pd.read_csv(content_file)
                else:
                    content_data = pd.read_excel(content_file)
                
                # Check if required columns exist
                required_gsc_cols = ['URL', 'Query', 'Clicks', 'Impressions']
                required_content_cols = ['URL', 'Content']
                
                missing_gsc_cols = [col for col in required_gsc_cols if col not in gsc_data.columns]
                missing_content_cols = [col for col in required_content_cols if col not in content_data.columns]
                
                if missing_gsc_cols or missing_content_cols:
                    if missing_gsc_cols:
                        st.error(f"GSC file is missing required columns: {', '.join(missing_gsc_cols)}")
                    if missing_content_cols:
                        st.error(f"Content file is missing required columns: {', '.join(missing_content_cols)}")
                else:
                    # Process GSC data to get top queries per page
                    gsc_data = gsc_data.sort_values(by=['URL', 'Clicks'], ascending=[True, False])
                    top_queries_per_page = {}
                    
                    for url in gsc_data['URL'].unique():
                        page_queries = gsc_data[gsc_data['URL'] == url].head(top_queries)
                        if not page_queries.empty:
                            top_queries_per_page[url] = page_queries['Query'].tolist()
                    
                    # Generate results
                    results = []
                    
                    # For each page in the content data
                    for _, content_row in content_data.iterrows():
                        source_url = content_row['URL']
                        source_content = str(content_row['Content'])
                        
                        # Skip if no content
                        if pd.isna(source_content) or not source_content.strip():
                            continue
                        
                        # Get top queries for this page
                        if source_url in top_queries_per_page:
                            queries = top_queries_per_page[source_url]
                            
                            # Extract keywords and generate variations
                            keywords = extract_keywords(source_content, nlp, keyword_extractor, top_n=top_queries)
                            all_keywords = list(set(queries + keywords))
                            keyword_variations = generate_semantic_variations(all_keywords, semantic_model, nlp)
                            
                            # For each destination page
                            for _, dest_row in content_data.iterrows():
                                dest_url = dest_row['URL']
                                dest_content = str(dest_row['Content'])
                                
                                # Skip self-links or empty content
                                if dest_url == source_url or pd.isna(dest_content) or not dest_content.strip():
                                    continue
                                
                                # Calculate base similarity between pages
                                base_similarity = calculate_similarity_score(
                                    preprocess_text(source_content)[:1000], 
                                    preprocess_text(dest_content)[:1000],
                                    semantic_model
                                )
                                
                                # Only consider pages with some similarity
                                if base_similarity >= 50:
                                    # For each potential anchor text
                                    for keyword in keyword_variations:
                                        # Skip very short keywords
                                        if len(keyword) < 3:
                                            continue
                                        
                                        # Calculate keyword to destination similarity
                                        keyword_dest_similarity = calculate_similarity_score(
                                            keyword, 
                                            preprocess_text(dest_content)[:1000],
                                            semantic_model
                                        )
                                        
                                        # Only consider if similarity is above threshold
                                        if keyword_dest_similarity >= similarity_threshold:
                                            # Extract context snippets
                                            snippets = extract_content_snippets(source_content, keyword)
                                            
                                            # Check if anchor already exists
                                            has_existing_anchor = check_existing_anchor(source_content, keyword, dest_url)
                                            
                                            # Generate suggestion if no anchor exists
                                            content_suggestion = ""
                                            if not has_existing_anchor and not snippets:
                                                content_suggestion = suggest_new_content(source_content, keyword)
                                            
                                            # If we have snippets or a suggestion
                                            if snippets or content_suggestion:
                                                # Get the best context snippet
                                                context = snippets[0] if snippets else "No direct match found in content."
                                                
                                                results.append({
                                                    'Source URL': source_url,
                                                    'Anchor Text': keyword,
                                                    'Similarity Score': keyword_dest_similarity,
                                                    'Destination URL': dest_url,
                                                    'Content Context': context,
                                                    'Existing Anchor?': 'Yes' if has_existing_anchor else 'No',
                                                    'New Content Suggestion': content_suggestion if not has_existing_anchor and not snippets else ""
                                                })
                    
                    # Convert to DataFrame and sort
                    if results:
                        df_results = pd.DataFrame(results)
                        df_results = df_results.sort_values(by=['Similarity Score'], ascending=False)
                        
                        # Limit results per page
                        page_counts = df_results['Source URL'].value_counts()
                        filtered_results = []
                        
                        for _, row in df_results.iterrows():
                            source_url = row['Source URL']
                            if page_counts[source_url] <= max_suggestions:
                                filtered_results.append(row)
                            else:
                                page_counts[source_url] -= 1
                        
                        final_results = pd.DataFrame(filtered_results)
                        
                        # Display results
                        st.header("Internal Linking Opportunities")
                        st.dataframe(final_results, use_container_width=True)
                        
                        # Download button
                        csv = final_results.to_csv(index=False)
                        st.download_button(
                            label="Download Results as CSV",
                            data=csv,
                            file_name="internal_linking_opportunities.csv",
                            mime="text/csv"
                        )
                    else:
                        st.warning("No linking opportunities found with the current settings. Try adjusting the similarity threshold or increasing the number of top queries.")

if __name__ == "__main__":
    main()
