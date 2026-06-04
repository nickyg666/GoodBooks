"""
Genre Filter - Remove adult/explicit content from public genre selectors
"""

# Genres to exclude from public dropdowns
EXCLUDED_GENRES = {
    'erotica',
    'erotic',
    'bdsm',
    'adult',
    'explicit',
    'hardcore',
    'pornography',
    'adult fiction',
    'adult contemporary'
}

def is_genre_allowed(genre):
    """Check if a genre is allowed (not in exclusion list)"""
    if not genre:
        return False
    return genre.lower().strip() not in EXCLUDED_GENRES

def filter_genres(genres_list):
    """Filter a list of genres, returning only allowed genres"""
    if not genres_list:
        return []
    return [g for g in genres_list if is_genre_allowed(g)]

def filter_genre_dict(genre_dict):
    """Filter a dictionary of genres (keys), returning only allowed genres"""
    if not genre_dict:
        return {}
    return {g: v for g, v in genre_dict.items() if is_genre_allowed(g)}

def get_excluded_genres():
    """Return a copy of the excluded genres set"""
    return EXCLUDED_GENRES.copy()
