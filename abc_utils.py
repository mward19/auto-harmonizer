import music21 as m21
import pandas as pd
import numpy as np
import re
import os
import typing
from tqdm import tqdm
from typing import Union
# this is used because there are some fractions floating somewhere
from fractions import Fraction
RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)
ROMAN_NUMERAL_MAP = {
    "-": -1,
    "Rest": 0, 

    "I": 1, "#I": 2, "bII": 2, "II": 3, "#II": 4, "bIII": 4, "III": 5, 
    "#III": 6, "bIV": 6, "IV": 6, "#IV": 7, "bV": 7, "V": 8, "#V": 9, 
    "bVI": 9, "VI": 10, "#VI": 11, "bVII": 11, "VII": 12, "bI": 12,

    "i": 13, "#i": 14, "bii": 14, "ii": 15, "#ii": 16, "biii": 16, "iii": 17, 
    "#iii": 18, "biv": 18, "iv": 18, "#iv": 19, "bv": 19, "v": 20, "#v": 21, 
    "bvi": 21, "vi": 22, "#vi": 23, "bvii": 23, "vii": 24, "bi": 24
}

CHORD_INT_MAP = {v: k for k, v in ROMAN_NUMERAL_MAP.items()}
    
def abc_to_dataframe(abc_text: str, 
                     chords_style='simple_numerals',
                     include_mode: bool=False,
                     return_key: bool=False):
    if chords_style != 'simple_numerals':
        raise Exception('Other chord styles have not been implemented.')

    # Prepare to collect chords and notes as `states`
    states_keys = ['measure', 'beat', 'chord', 'melody']
    states = {key: [] for key in states_keys}

    def _append_line(
            measure: int, 
            beat: float, 
            chord: m21.harmony.ChordSymbol, 
            melody: Union[m21.note.Note, m21.note.Rest],
            key: m21.key.Key,
            include_mode: bool = False
        ):
        """
        Append a new state to the dictionary `states`.

        The "melody" added to the dictionary will be 0 if `melody` is a rest,
        and between 1 and 139 if it is a note.

        The "chord" added to the dictionary will be 0 if there is no chord, and
        between 1 and 24 otherwise, as determined by ROMAN_NUMERAL_MAP.
        """
        chord_repr = chord.romanNumeral.romanNumeral if chord else '-'
        
        # If rest, use a -1 (MIDI mins at 0)
        if not melody:
            melody_repr = 0
        elif isinstance(melody, m21.note.Rest):
            melody_repr = 0
        elif isinstance(melody, m21.note.Note):
            # TODO: add more modes.
            mode_shift = 0  
            if include_mode:
                mode = key.mode
                if mode == 'major':
                    mode_shift = 0
                elif mode == 'minor':
                    mode_shift = 9 # Shift up to avoid negative values (rest is 0)
                else:
                    raise Exception(f'Mode is {mode}. Not major or minor')
            key_shift = 12 - (key.tonic.midi % 12) + mode_shift
            
            melody_repr = melody.pitch.midi + key_shift + 1 # Add 1 to make room so that 0 can represent a rest
        else:
            raise Exception('Melody should be a rest or a note')
            
        states['measure'].append(measure)
        states['beat'].append(beat)
        states['chord'].append(ROMAN_NUMERAL_MAP[chord_repr])
        states['melody'].append(melody_repr) 

    # Variables to collect the pieces of a new "state" before appending it to the `states` dictionary
    current_key = None
    current_melody = None
    current_chord = None
    current_measure = None
    current_beat = None
    
    def not_meaningful(element):
        """ Check if we want to bother processing an element. """
        return isinstance(
            element,
            (
                m21.spanner.RepeatBracket,
                m21.bar.Repeat
            )
        )
    
    # Iterate through part, collecting states
    part = m21.converter.parse(abc_text, format='abc').parts[0]
    for index, element in enumerate(part.flatten()):
        if not_meaningful(element): 
            continue
            
        this_beat = element.beat
        this_measure = element.measureNumber

        # The first time an object has measure and beat, use that to initialize current_beat and current_measure
        if current_beat is None and current_measure is None and this_beat is not None and this_measure is not None:
            current_beat = this_beat
            current_measure = this_measure

        # If the time in the music has changed, append the old stuff
        if (this_measure != current_measure or this_beat != current_beat) and (current_chord is not None or current_melody is not None): 
            # Append the last new state
            _append_line(
                current_measure,
                current_beat,
                current_chord,
                current_melody,
                current_key
            )
            # Update current measure
            current_measure = this_measure
            current_beat = this_beat
        
        
        if isinstance(element, m21.key.Key):
            if current_key is None:
                song_key = element.__str__()
            current_key = element

        elif isinstance(element, m21.harmony.ChordSymbol):
            # Make sure the chord is relative to the current key
            if current_key is None:
                raise Exception('Current key unknown')
            
            element.key = current_key
            current_chord = element
            
        elif isinstance(element, (m21.note.Note, m21.note.Rest)):
            if current_key is None:
                raise Exception('Current key unknown')
            
            element.key = current_key
            current_melody = element
    
    states_df = pd.DataFrame(states).astype({'melody': int})

    states_df['first_key_in_song'] = song_key

    if return_key:
        return states_df, song_key
    else:
        return states_df

def dataframe_to_states(song_df: pd.DataFrame, chords_per_state: int, melody_per_state: int):
    """
    Convert a dataframe created by `abc_to_dataframe` to a numpy matrix of ints
    representing the state over time, where the state can encode the "history"
    of the chord and melody to some degree determined by `chords_per_state` and
    `melody_per_state`.
    """
    if melody_per_state == 0 and chords_per_state == 0:
        raise Exception('Cannot return empty state array')
    elif melody_per_state < 0 or chords_per_state < 0:
        raise Exception('Cannot process negative numbers of chords or melody notes')
        
    n_rows = len(song_df) + 1  # +1 allows initial row to be empty
    
    # Initialize chord and melody states, handling cases where they might be 0
    chord_states = np.empty((n_rows, chords_per_state), dtype=int) if chords_per_state > 0 else np.empty((n_rows, 0), dtype=int)
    melody_states = np.empty((n_rows, melody_per_state + 1), dtype=int) if melody_per_state > 0 else np.empty((n_rows, 0), dtype=int)
    observations = np.empty(n_rows)

    if chords_per_state > 0:
        chord_states[0, :] = -1  # Start with zeros (rests)
    if melody_per_state > 0:
        melody_states[0, :] = -1  # Start with zeros (rests)
    observations[0] = 0


    for i, (_, row) in tqdm(enumerate(song_df.iterrows()), total=len(song_df), desc='Processing states'):
        if chords_per_state > 0:
            chord_states[i+1, 0:-1] = chord_states[i, 1:]  # Shift left
            chord_states[i+1, -1] = row['chord']  # Append new chord
        
        if melody_per_state > 0:
            melody_states[i+1, 0:-1] = melody_states[i, 1:]  # Shift left
            melody_states[i+1, -1] = row['melody']  # Append new melody
        
        observations[i+1] = row['melody']
    
    # Don't include the current melody note (melody_states[:, :-1]))

    return np.hstack([chord_states, melody_states[:, :-1]]), observations

def redact(seq, lam):
    """
    Redact a sequence of chords to only contain lam repetitions of any given chord in the sequence. Takes in a one-dimensional sequence and returns the 
    shortened sequence
    """
    if len(seq.shape) != 1:
        raise TypeError("array must be 1-dimensional")

    # start building a mask for the sequence: True if we are below lam repetitions, False otherwise
    curr_val = seq[0]
    length = 1
    mask = [True]

    # iterate through and create the mask
    for i in range(1, len(seq)):
        if seq[i] == curr_val:
            if length <= lam - 1:
                mask.append(True)
            else:
                mask.append(False)
            length += 1
        else:
            mask.append(True)
            curr_val = seq[i]
            length = 1

    # mask out the values and return
    mask = np.array(mask)
    return seq[mask]

def states_to_transition(states: np.ndarray, observations: np.ndarray = None, lam: int=None, trans_prior: float=0.1, emissions_prior: float=0.1):
    """ 
    Given a matrix `states` where each row represents a new state, a vector
    or matrix `observations` representing the associated observations, and an integer lam 
    representing the number of repetitions wanted for the simple 1-chord case, return: 
     - a column-stochastic transition matrix
     - a column-stochastic emission probability matrix
     - a matrix where each row is the hidden state associated with that
       row/column in the transition matrix
     - a matrix where each row is the observation associated with that
       row in the emission probability matrix
    """
    # Get all unique states. This matrix maps integers to states as in `states`
    unique_states = np.unique(states, axis=0)

    states_to_index = {tuple(state): i for i, state in enumerate(unique_states)}
    states_to_index['<UNKNOWN>'] = len(unique_states)

    # Represent all states as unique integers
    states_as_int = np.array([states_to_index[tuple(state)] for i, state in enumerate(states)])

    # if the states are one-dimensional (only one chord), then perform the slicing action
    if ((1 in states_as_int.shape or len(states_as_int.shape) == 1) and (lam is not None)):
        new_states = redact(states_as_int, lam)
        state_pairs = np.stack([new_states[:-1], new_states[1:]], axis=1)
    else:
        # Get pairs of consecutive states to prepare to calculate transition probabilities
        state_pairs = np.stack([states_as_int[:-1], states_as_int[1:]], axis=1)
    
    # Count number of transitions of each kind
    transition_type, n_transition_type = np.unique(state_pairs, return_counts=True, axis=0)
    n_transitions_total = np.sum(n_transition_type)
    
    # Make transition matrix between hidden states
    transition_matrix = np.ones((len(unique_states), len(unique_states))) * trans_prior # TODO: Add a parameter for this
    for (from_state, to_state), count in zip(transition_type, n_transition_type):
        transition_matrix[to_state, from_state] += count
    
    # Normalize transition matrix to be column stochastic
    transition_matrix /= np.sum(transition_matrix, axis=0)
    transition_matrix = np.nan_to_num(transition_matrix, nan=0.)

    if observations is None:
        return transition_matrix, unique_states
    # If observations is a row vector, make it a true column vector
    observations = observations.reshape(observations.shape[0], -1)

    # Create emission probability matrix
    unique_obs = np.unique(observations, axis=0)
    observation_to_index = {tuple(obs): i for i, obs in enumerate(unique_obs)}
    obs_indexed = np.array([observation_to_index[tuple(obs)] for i, obs in enumerate(observations)])

    # Count number of observations corresponding to each state
    state_obs_pairs = np.stack([states_as_int, obs_indexed], axis=1)
    obs_with_state, n_obs_with_state = np.unique(state_obs_pairs, return_counts=True, axis=0)

    emission_probs = np.ones((len(unique_obs), len(unique_states))) * emissions_prior # TODO: add a parameter for this
    for (state, obs), count in zip(obs_with_state, n_obs_with_state):
        emission_probs[obs, state] += count
    
    # Normalize emission probability matrix to be column stochastic
    emission_probs /= np.sum(emission_probs, axis=0)
    emission_probs = np.nan_to_num(emission_probs, nan=0.)

    # TODO: bandaid fix
    states_with_no_transition = np.where(np.sum(transition_matrix, axis=0) == 0)[0]

    if states_with_no_transition.size > 0:
        transition_to_beginning = np.zeros((transition_matrix.shape[0], 1))  # Reshape to column vector
        transition_to_beginning[0, 0] = 1
        transition_matrix[:, states_with_no_transition] = transition_to_beginning


    return transition_matrix, emission_probs, unique_states, unique_obs, states_to_index, observation_to_index

def dataset_to_abc(dataset_abc_text: str, label, reference_number):
    """ 
    Given a poorly formatted string of ABC from the dataset, reformats metadata
    so that ABC readers can parse it well.
    """
    # Remove extra whitespace
    fixed_text = dataset_abc_text.strip()
    # Remove task tag
    fixed_text = re.sub(r'^%%\w+\s*', '', fixed_text)
    # Split metadata from the tune
    metadata, tune = fixed_text.split('|', 1)
    # Add newlines to metadata only where needed (after the first occurrence of each metadata key)
    metadata = re.sub(r"(\S:\S+)(?=\s)(?!\n)", r"\1\n", metadata)
    
    # Add reference number and title (only add if they don't exist already)
    if not re.search(r'^\s*X:', metadata):
        metadata = f'X:{reference_number}\n' + metadata
    if not re.search(r'^\s*T:', metadata):
        metadata = f'T:{label} {reference_number}\n' + metadata
    
    ## Fix key changes throughout tune
    ## TODO: still not processing key changes in abc_to_dataframe
    #key_regex = re.compile(r'\[K:([^\]]{1,2})\]')
    #tune = key_regex.sub(r'\n[K:\1]\n', tune)

    fixed_text = metadata + '|' + tune
    return fixed_text

def load_dataset_df(*, train: bool = True, local: bool = True) -> pd.DataFrame:
    """ 
    Loads the melodyhub dataset from HuggingFace as a pandas DataFrame. Must pip
    install `fsspec` and `huggingface_hub`.

    Keyword parameters:

    - train (bool): True for the train set, False for the validation set

    - local (bool): True to use the data in `./melodyhub`, False to download directly
    from HuggingFace 
    """
    splits = {'train': 'train.jsonl', 'validation': 'validation.jsonl'}
    # Select dataset based on `train` parameter
    split = 'train' if train else 'validation'
    # Set path based on `local` parameter
    path = "./melodyhub/" if local else "hf://datasets/sander-wood/melodyhub/"

    dataset = pd.read_json(
        path + splits[split],
        lines=True
    )
    return dataset

def load_datasets(test_ratio=0.3, 
                  val_ratio=0.2, 
                  return_train_val=True,
                  return_test=False,
                  recreate_dataset=False, 
                  local=True):
    """ 
    Loads both train, val, and test dataset each of which come preprocessed by
    the 'abc_to_dataframe' function in `abc_utils.py`. Note the 
    the lines for the 'harmonization' task. Puts the ABC input column into
    standard ABC format, with appropriate newlines.

    Parameters:
    -------
    test_ratio (float): The amount of the total original dataset that goes to the test subset
    val_ratio (float): the amount of the non-test subset (the train set) goes to the validation split
    return_train_val (bool): return train and val dataframes (see return)
    return_test (bool): return test dataframes (see return)
    recreate_dataset (bool): reruns the actual creatation of the dataset split
    local (bool): used in load_harminization_train_test (only used if recreate_dataset=True)

    Returns:
    --------
    list_to_return (list of pd.DataFrames): 
        If return_train_val=True this returns 
        (train_df, train_len_series, train_indicies_series, val_df, val_len_series, val_indicies_series)
        if return_test=True then (test_df, test_len_series, test_indicies_series) 
        is also appended to the list_to_return

    """
    # find the ratio to split the data
    train_ratio = np.round((1-test_ratio) * (1-val_ratio), 2)
    val_ratio = np.round((1-test_ratio) * (val_ratio), 2)

    dataset_path = './curated_datasets'
    train_path = os.path.join(dataset_path, f'train_{train_ratio}.parquet')
    val_path = os.path.join(dataset_path, f'val_{val_ratio}.parquet')
    test_path = os.path.join(dataset_path, f'test_{test_ratio}.parquet')

    # These are the complementary path with lengths
    train_len_path = os.path.join(dataset_path, f'train_{train_ratio}_lengths.csv')
    val_len_path = os.path.join(dataset_path, f'val_{val_ratio}_lengths.csv')
    test_len_path = os.path.join(dataset_path, f'test_{test_ratio}_lengths.csv')

    # These are the complementary song_index to og song dataframe
    train_good_indicies_path = os.path.join(dataset_path, f'train_{train_ratio}_song_indicies.csv')
    val_good_indicies_path = os.path.join(dataset_path, f'val_{val_ratio}_song_indicies.csv')
    test_good_indicies_path = os.path.join(dataset_path, f'test_{test_ratio}_song_indicies.csv')

    # this is where the bad songs will go 
    train_bad_path = os.path.join(dataset_path, f'train_{train_ratio}_bad_songs.csv')
    val_bad_path = os.path.join(dataset_path, f'val_{val_ratio}_bad_songs.csv')
    test_bad_path = os.path.join(dataset_path, f'test_{test_ratio}_bad_songs.csv')

    if not os.path.exists(dataset_path):
        os.makedirs(dataset_path)

    if (recreate_dataset) or ( 
        (not os.path.isfile(train_path)) or 
        (not os.path.isfile(val_path)) or 
        (not os.path.isfile(test_path))):

        print("Loading Harmonization train and test songs")
        # Load old_train and val set from melody hub or local
        old_train_set, old_val_set = load_harmonization_train_test(local=local)
        full_set = pd.concat([old_train_set, old_val_set], ignore_index=True)
        # print(type(full_set))
        len_of_full_set = len(full_set)
        draw = np.arange(len_of_full_set)

        np.random.shuffle(draw)

        train_size = int(len_of_full_set*train_ratio)
        val_size = int(len_of_full_set*val_ratio)
        train_slice = np.s_[:train_size]
        val_slice = np.s_[train_size:train_size+val_size]
        test_slice = np.s_[train_size+val_size:]

        all_good_songs = []
        all_1st_keys = []

        for slice, name, df_path, len_path, good_index_path, bad_path in [(train_slice, "train_set", train_path, train_len_path, train_good_indicies_path, train_bad_path), 
                                  (val_slice, "val_set", val_path, val_len_path, val_good_indicies_path, val_bad_path), 
                                  (test_slice, "test_set", test_path, test_len_path, test_good_indicies_path, test_bad_path)]:
            preprocessed_set = []
            set_lengths = []
            bad_songs = []
            good_songs = []

            print(f'Making {name}')
            for i in tqdm(draw[slice]):
                song = full_set['output'].loc[i]
                try:
                    song_df, song_key = abc_to_dataframe(song, return_key=True)
                    all_1st_keys.append(song_key)
                    preprocessed_set.append(song_df)
                    set_lengths.append(len(song_df))
                    del song_df
                    good_songs.append(i)
                    all_good_songs.append(i)
                except:
                    print('bad')
                    bad_songs.append(i)
                
            print(f'Saving {name}')
            # save the good song dataset
            df = pd.concat(preprocessed_set)
            df.beat = df.beat.apply(float)
            df.to_parquet(df_path)

            # save the lengths of the good songs
            len_series = pd.Series(set_lengths, name="lengths")
            len_series.to_csv(len_path, index=False)

            # save the indicies of the good songs
            good_i_series = pd.Series(good_songs, name="song_index")
            good_i_series.to_csv(good_index_path, index=False)

            # save the series of the bad songs
            bad_series = pd.Series(bad_songs, name='song_index')
            bad_series.to_csv(bad_path, index=False)

        # save the original dataset (excluding the indicies that didn't work)
        # including the first key in the song as a column
        full_set = full_set.loc[all_good_songs]
        full_set['keys'] = all_1st_keys
        full_set.to_parquet(os.path.join(dataset_path, f'full_dataset_w_key_{train_ratio}.parquet'))

    # get main dfs
    test_df = pd.read_parquet(test_path)
    val_df = pd.read_parquet(val_path)
    train_df = pd.read_parquet(train_path)

    # get the lengths
    train_len_series = pd.read_csv(train_len_path)
    val_len_series = pd.read_csv(val_len_path)
    test_len_series = pd.read_csv(test_len_path)

    # get the indicies
    train_indicies_series = pd.read_csv(train_len_path)
    val_indicies_series = pd.read_csv(val_len_path)
    test_indicies_series = pd.read_csv(test_len_path)

    list_to_return = []
    if return_train_val:
        list_to_return.append(train_df)
        list_to_return.append(train_len_series)
        list_to_return.append(train_indicies_series)
        list_to_return.append(val_df)
        list_to_return.append(val_len_series)
        list_to_return.append(val_indicies_series)

    if return_test:
        list_to_return.append(test_df)
        list_to_return.append(test_len_series)
        list_to_return.append(test_indicies_series)

    return list_to_return



def load_harmonization_train_test(local=True):
    """ 
    Loads both train and test (validation) melodyhub datasets, returning only
    the lines for the 'harmonization' task. Puts the ABC input column into
    standard ABC format, with appropriate newlines.
    """
    train_set = load_dataset_df(train=True, local=local)
    test_set = load_dataset_df(train=False, local=local)

    train_set = train_set[train_set['task'] == 'harmonization']
    test_set = test_set[test_set['task'] == 'harmonization']

    for label, dataset in [('Train tune', train_set), ('Test tune', test_set)]:
        # Format tune metadata correctly
        dataset['input'] = dataset.apply(
            lambda row: dataset_to_abc(row['input'], label, row.name), axis=1
        )
        dataset['output'] = dataset.apply(
            lambda row: dataset_to_abc(row['output'], label, row.name), axis=1
        )
        
    return train_set, test_set
class OG_Dataset(object):
    '''An object for intereacting with the original melodyhub dataset'''

    def __init__(self, 
                 full_dataset_dir: str = "./curated_datasets",
                 full_dataset_filename: str = "full_dataset_w_key_0.56.parquet",
                 curated_datasets_dir: str = "./curated_datasets",
                 train_songs_ind_filename: str = "train_0.56_song_indicies.csv",
                 val_songs_ind_filename: str = "val_0.14_song_indicies.csv",
                 test_songs_ind_filename: str = "test_0.3_song_indicies.csv"):
        
        self.og_full_dataset = pd.read_parquet(os.path.join(full_dataset_dir, full_dataset_filename))
        self.og_full_dataset = self.og_full_dataset.convert_dtypes()


        # These are the paths complementary song_index to og song dataframe
        train_indicies_path = os.path.join(curated_datasets_dir, train_songs_ind_filename)
        val_indicies_path = os.path.join(curated_datasets_dir, val_songs_ind_filename)
        test_indicies_path = os.path.join(curated_datasets_dir, test_songs_ind_filename)

        self.og_train_indicies = pd.read_csv(train_indicies_path)
        self.og_val_indicies = pd.read_csv(val_indicies_path)
        self.og_test_indicies = pd.read_csv(test_indicies_path)

        good_song_indicies = pd.concat([self.og_train_indicies,
                                        self.og_val_indicies,
                                        self.og_test_indicies])

        self.og_full_dataset = self.og_full_dataset.loc[good_song_indicies.values.flatten().tolist()]

        # add split type to og_full_dataset
        self.og_full_dataset['split_type'] = None
        self.og_full_dataset.loc[self.og_train_indicies.values.flatten().tolist(), 'split_type'] = 'train'
        self.og_full_dataset.loc[self.og_val_indicies.values.flatten().tolist(), 'split_type'] = 'val'
        self.og_full_dataset.loc[self.og_test_indicies.values.flatten().tolist(), 'split_type'] = 'test'

        self.cur_to_og_indicies = {split_type:{cur_ind:og_indicies.iloc[cur_ind].item() for cur_ind in range(len(og_indicies))} for split_type, og_indicies in [('train', self.og_train_indicies), ('val', self.og_val_indicies), ('test', self.og_test_indicies)]}
        self.og_to_cur_indicies = {split_type:{og_ind:cur_ind for cur_ind, og_ind in ind_dict.items()} for split_type, ind_dict in self.cur_to_og_indicies.items()}

    def get_og_rows_from_cur_indicies(self, cur_indicies: list = None, split_type='train'):
        ''' Get subsets of the original train, val or test dataset to access the og abc_texts
        
        Parameters:
        -----------
        train_indicies (list): A list of indicies (based on the preprocessed 
                               train dataset) that the user wants to access
        val_indicies (list): A list of indicies (based on the preprocessed 
                               val dataset) that the user wants to access
        test_indicies (list): A list of indicies (based on the preprocessed 
                               test dataset) that the user wants to access

        Returns:
        --------
        (og_train_subset, og_val_subset, og_test_subset): 
            where og_{train/val/test}_subset is a subset of the original dataframe 
            that contains the output and input abc texts corresponding to the indicies
            that were passed

        '''
        og_indicies = [self.cur_to_og_indicies[split_type][i] for i in cur_indicies]
        og_subset = self.og_full_dataset.loc[og_indicies]

        return og_subset
    
    def load_song_subset(self, states_df, song_lengths, song_indices):
        """
        Create a subset of the train"""
        end_positions = np.cumsum(song_lengths)
        positions = np.insert(end_positions, 0, np.array([0]))
        songs = []
        for i in song_indices:
            song = states_df.iloc[positions[i] : positions[i+1]]
            songs.append(song)

        # return songs and lengths
        return pd.concat(songs), song_lengths.iloc[song_indices]
    
    def filter_df(self,
              states_df: pd.DataFrame, 
              song_lengths: pd.Series, 
              col_to_filter: str='keys',
              filter_str: str = 'major',
              regex: bool = True,
              split_type: str = 'train'):
        """
        Filters the current_datasets (or preprocessed datasets) using information
        from the original full dataset. For instance if you want to get the songs 
        in major keys it will return a subset of states_df containing only the
        major keys.

        Params:
        --------
        states_df (pd.DataFrame): This is a dataframe containing the states such
                                  as train_df from load_datasets()
        song_lengths (pd.Series): This is a series containing the lengths of the
                                  songs contained in the train_df from 
                                  load_datasets() (such as train_lengths)
        col_to_filter (str): The column from og_dataset that you want to filter 
                             on.
        filter_str (str): The string you filter by in the column specified by
                          col_to_filter.
        regex (bool): This turns regex on or off that is used in the
                       df.str.contains("string", regex=regex)
        split_type (str): this specifies which split of the data that is being
                          used i.e. train/val/test

        Return:
        ------
        (subset_states_df, subset_song_lengths) subset_states_df is the filtered 
        song dataframe and the subset_song_lengths is the lengths of the songs in
        the subset_states_df        
        """
        og_indicies = self.og_full_dataset.loc[self.og_full_dataset[col_to_filter].str.contains(filter_str, regex) & (self.og_full_dataset['split_type']==split_type)].index
        cur_indicies = [self.og_to_cur_indicies[split_type][og_ind] for og_ind in og_indicies]
        
        return self.load_song_subset(states_df, song_lengths, cur_indicies)


if __name__ == '__main__':
    print(load_datasets())

    # train_set, _ = load_harmonization_train_test()
    # bad_songs = []
    # for i, row in tqdm(list(train_set.iterrows())):
    #     try:
    #         abc_to_dataframe(row['output'])
    #     except:
    #         bad_songs.append(row)
    #         print(row['output'])

    # df = abc_to_dataframe(train_set.iloc[12]['output'])
    # with open('bad_songs.txt', 'a') as file:
    #     file.write(str([song.index for song in bad_songs]))
    #     for song in bad_songs:
    #         file.write(song['output'])

    # print(dataframe_to_states(df, 3, 2))
    # print()
