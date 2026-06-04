# Genre Filter Implementation
# Purpose: Filter out adult/sexual genres from genre selector
# Location: genre_filter.py

# Genres to block from selection
BLOCKED_GENRES = {
    'Erotica',
    'BDSM',
    'Adult',
    'Adult Content',
    'Explicit',
    'Sexual Content',
    'Porn',
    'Pornography',
    'XXX',
    'Sex',
    'Fetish',
    'Domination',
    'Submission',
    'Explicit Content',
    'Erotic Fiction',
    'Adult Fiction',
    'Erotic Romance',
    'Steamy Romance',
}

# Genres that ARE allowed (Romance is OK)
ALLOWED_GENRES = {
    'Romance',
    'Contemporary Romance',
    'Historical Romance',
    'Paranormal Romance',
    'Science Fiction Romance',
    'Fantasy Romance',
    'Young Adult Romance',
}

def is_genre_blocked(genre_name):
    """
    Check if a genre is blocked.
    
    Args:
        genre_name: Name of the genre to check
    
    Returns:
        True if genre is blocked, False if allowed
    """
    if not genre_name:
        return False
    
    genre_lower = genre_name.strip().lower()
    
    # Check explicitly blocked genres
    for blocked in BLOCKED_GENRES:
        if blocked.lower() == genre_lower:
            return True
    
    # Check if it contains blocked keywords
    blocked_keywords = ['erotic', 'explicit', 'adult', 'bdsm', 'porn', 'fetish', 'sex']
    for keyword in blocked_keywords:
        if keyword in genre_lower:
            return True
    
    return False

def filter_genres(genre_list):
    """
    Filter a list of genres, removing blocked ones.
    
    Args:
        genre_list: List of genre names
    
    Returns:
        List of filtered (allowed) genre names
    """
    if not genre_list:
        return []
    
    return [g for g in genre_list if not is_genre_blocked(g)]

def get_filtered_genre_options(genre_stats_dict):
    """
    Filter genre options from a dictionary of genre statistics.
    Used for genre selector in library view.
    
    Args:
        genre_stats_dict: Dictionary with genres as keys and counts as values
    
    Returns:
        Dictionary with only non-blocked genres
    """
    if not genre_stats_dict:
        return {}
    
    filtered = {}
    for genre, count in genre_stats_dict.items():
        if not is_genre_blocked(genre):
            filtered[genre] = count
    
    return filtered

def get_safe_genre_name(genre_name):
    """
    Get a safe version of a genre name, or return None if blocked.
    Useful for single genre checks.
    
    Args:
        genre_name: Name of genre
    
    Returns:
        Genre name if allowed, None if blocked
    """
    if is_genre_blocked(genre_name):
        return None
    return genre_name
