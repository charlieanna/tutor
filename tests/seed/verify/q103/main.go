package main

func main() {
	ch := make(chan int, 1)
	close(ch)
	ch <- 9
}
