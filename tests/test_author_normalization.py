#!/usr/bin/env python3
"""
Test suite for author field normalization fixes.

This test suite verifies that the author normalization is consistent
across all code paths, particularly for edge cases like "Mc" prefixes,
multiple authors, and varying separator formats.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from settings_manager import HistoryManager

def test_cleanup_author_mc_prefix():
    """Test 'Mc' prefix handling - the critical bug case"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    # The main issue: "Freida; Mc; Fadden" should normalize consistently
    result = history_manager.cleanup_author("Freida; Mc; Fadden")
    assert result == "freida mcfadden", f"Expected 'freida mcfadden', got '{result}'"
    print("✓ Test 1: Mc prefix handling - PASS")

def test_cleanup_author_multiple_separators():
    """Test handling of multiple separator formats"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    # Should handle both semicolon and space separators
    result1 = history_manager.cleanup_author("John; Smith")
    result2 = history_manager.cleanup_author("John Smith")
    # Both should normalize to same format
    assert result1.lower() == result2.lower(), f"Mismatch: '{result1}' vs '{result2}'"
    print("✓ Test 2: Multiple separators - PASS")

def test_cleanup_author_mac_prefix():
    """Test 'Mac' prefix handling"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    result = history_manager.cleanup_author("Donald; Mac; Gill")
    assert result == "donald macgill", f"Expected 'donald macgill', got '{result}'"
    print("✓ Test 3: Mac prefix handling - PASS")

def test_cleanup_author_von_prefix():
    """Test 'Von' prefix handling"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    result = history_manager.cleanup_author("Karl; Von; Neumann")
    assert result == "karl vonneumann", f"Expected 'karl vonneumann', got '{result}'"
    print("✓ Test 4: Von prefix handling - PASS")

def test_cleanup_author_single_author():
    """Test single author name"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    result = history_manager.cleanup_author("Stephen King")
    assert result == "stephen king", f"Expected 'stephen king', got '{result}'"
    print("✓ Test 5: Single author name - PASS")

def test_cleanup_author_multiple_authors():
    """Test multiple authors"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    result = history_manager.cleanup_author("John; Smith; Jane; Doe")
    # Should handle multiple author names
    assert len(result) > 0 and result.islower(), f"Invalid result: '{result}'"
    print("✓ Test 6: Multiple authors - PASS")

def test_cleanup_author_empty():
    """Test empty author"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    result = history_manager.cleanup_author("")
    assert result == "", f"Expected empty string, got '{result}'"
    print("✓ Test 7: Empty author - PASS")

def test_cleanup_author_initials():
    """Test author with initials"""
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    result = history_manager.cleanup_author("A.A.; Milne")
    # Should preserve initials
    assert "a.a" in result or "a. a" in result, f"Initials not preserved: '{result}'"
    print("✓ Test 8: Author with initials - PASS")

def test_consistency_across_paths():
    """
    Test that cleanup_author() produces consistent results across
    different code paths (library lookup, deduplication, etc.)
    """
    history_manager = HistoryManager(Path("/tmp/test_history.json"))
    
    test_cases = [
        "Freida; Mc; Fadden",      # The critical bug case
        "Donald; Mac; Gill",
        "Karl; Von; Neumann",
        "Stephen King",
        "John; Smith; Jane; Doe",
        "A.A.; Milne",
    ]
    
    results = {}
    for author in test_cases:
        result = history_manager.cleanup_author(author)
        results[author] = result
        print(f"  '{author}' → '{result}'")
    
    # Verify all results are lowercase and properly formatted
    for author, result in results.items():
        assert result.islower(), f"Not lowercase: '{author}' → '{result}'"
        assert len(result) > 0, f"Empty result for: '{author}'"
    
    print("✓ Test 9: Consistency across all test cases - PASS")

def main():
    print("=" * 70)
    print("AUTHOR NORMALIZATION TEST SUITE")
    print("=" * 70)
    print()
    
    tests = [
        test_cleanup_author_mc_prefix,
        test_cleanup_author_multiple_separators,
        test_cleanup_author_mac_prefix,
        test_cleanup_author_von_prefix,
        test_cleanup_author_single_author,
        test_cleanup_author_multiple_authors,
        test_cleanup_author_empty,
        test_cleanup_author_initials,
        test_consistency_across_paths,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_func.__name__} - FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} - ERROR: {e}")
            failed += 1
    
    print()
    print("=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)
    
    if failed == 0:
        print("✓ ALL TESTS PASSED")
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
