# AI prompt

Here is the prompt to ask AI to generate the code for us

Input: I have the satellite network topology graph, which are stored using the data structure created by `networkx.Graph()`. In this graph, each node is a container, representing a satellite, a ground station, or a user terminal. Intially, these containers are not connected, or are connected using the previous topology. 

What I want to do: I am now connecting these containers based on the new topology. I want to use two different approaches and compare them. Please write programs for me, one for each approach:
- Approach 1: use Linux bridge to connect satellites.
- Approach 2: use the approach used by OpenSN, i.e., using ebpf. 

